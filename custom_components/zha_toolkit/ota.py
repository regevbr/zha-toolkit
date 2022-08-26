import json
import logging
import os
from glob import glob

import aiohttp

from . import DEFAULT_OTAU
from .params import INTERNAL_PARAMS as p

LOGGER = logging.getLogger(__name__)
KOENKK_LIST_URL = (
    "https://raw.githubusercontent.com/Koenkk/zigbee-OTA/master/index.json"
)


async def download_koenkk_ota(listener, ota_dir):
    # Get all FW files that were already downloaded.
    # The files usually have the FW version in their name, making them unique.
    ota_glob_expr = [
        "*.ZIGBEE",
        "*.OTA",
        "*.sbl-ota",
        "*.bin",
        "*.ota",
        "*.zigbee",
    ]

    # Dictionary to do more efficient lookups
    ota_files_on_disk = {}
    for glob_expr in ota_glob_expr:
        for path in glob(glob_expr):
            ota_files_on_disk[path] = True

    # Get manufacturers
    manfs = {}
    for info in [
        device.zha_device_info for device in listener.devices.values()
    ]:
        manfs[info["manufacturer_code"]] = True

    # var_dump(ota_files_on_disk)
    new_fw_info = {}
    async with aiohttp.ClientSession() as req:
        LOGGER.debug("Get Koenkk FW list")
        async with req.get(KOENKK_LIST_URL) as rsp:
            data = json.loads(await rsp.read())
            for fw_info in data:
                if fw_info["url"]:
                    filename = fw_info["url"].split("/")[-1]
                    # Try to get fw corresponding to device manufacturers
                    if (
                        fw_info["manufacturerCode"] in manfs
                    ):  # or filename not in ota_files_on_disk:
                        # Contains manufacturerCode which can be used to check
                        # files that are meaningful to download
                        new_fw_info[filename] = fw_info

    for filename, fw_info in new_fw_info.items():
        async with aiohttp.ClientSession() as req:
            url = fw_info["url"]
            try:
                LOGGER.debug("Get '%s'", url)
                async with req.get(url) as rsp:
                    data = await rsp.read()

                out_filename = os.path.join(ota_dir, filename)

                with open(out_filename, "wb") as ota_file:
                    LOGGER.debug("Try to write '%s'", out_filename)
                    ota_file.write(data)
            except Exception as e:
                LOGGER.warning("Exception getting '%s': %s", url, e)

        break  # Just get one during debug


async def ota_update_images(
    app, listener, ieee, cmd, data, service, params, event_data
):
    for _, (ota, _) in app.ota._listeners.items():
        await ota.refresh_firmware_list()


async def ota_notify(
    app, listener, ieee, cmd, data, service, params, event_data
):
    if params[p.DOWNLOAD]:
        # Download FW from koenkk's list
        if params[p.PATH]:
            ota_dir = params[p.PATH]
        else:
            ota_dir = DEFAULT_OTAU

        download_koenkk_ota(listener, ota_dir)

    # Update internal image database
    await ota_update_images(
        app, listener, ieee, cmd, data, service, params, event_data
    )

    if ieee is None:
        LOGGER.error("missing ieee")
        return

    LOGGER.debug("running 'image_notify' command: %s", service)

    device = app.get_device(ieee=ieee)

    cluster = None
    for epid, ep in device.endpoints.items():
        if epid == 0:
            continue
        if 0x0019 in ep.out_clusters:
            cluster = ep.out_clusters[0x0019]
            break
    if cluster is None:
        LOGGER.debug("No OTA cluster found")
        return
    basic = device.endpoints[cluster.endpoint.endpoint_id].basic
    await basic.bind()
    ret = await basic.configure_reporting("sw_build_id", 0, 1800, 1)
    LOGGER.debug("Configured reporting: %s", ret)
    ret = await cluster.image_notify(0, 100)

    LOGGER.debug("Sent image notify command to 0x%04x: %s", device.nwk, ret)
