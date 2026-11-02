# Run through run_console.ps1 while a person physically reconnects the camera.
# Verifies DECXIN identity and 10 actual 720p frames after every reconnect.
# Native 30fps mismatch remains reported; this script certifies link recovery only.
hotplug_check() {
    for cycle in 1 2 3; do
        echo "HOTPLUG_WAIT_REMOVE cycle=$cycle"
        waited=0
        while [ -e /sys/bus/usb/devices/1-1/idVendor ] && [ "$waited" -lt 180 ]; do sleep 1; waited=$((waited+1)); done
        if [ -e /sys/bus/usb/devices/1-1/idVendor ]; then echo "HOTPLUG_FAIL removal_timeout cycle=$cycle"; return 1; fi
        echo "HOTPLUG_REMOVED cycle=$cycle"
        waited=0
        while [ ! -e /sys/bus/usb/devices/1-1/idVendor ] && [ "$waited" -lt 60 ]; do sleep 1; waited=$((waited+1)); done
        if [ ! -e /sys/bus/usb/devices/1-1/idVendor ]; then echo "HOTPLUG_FAIL reconnect_timeout cycle=$cycle"; return 1; fi
        sleep 2
        if [ ! -e /sys/bus/usb/devices/1-1/idVendor ]; then echo "HOTPLUG_FAIL disconnected_before_sample cycle=$cycle"; return 1; fi
        vendor=$(cat /sys/bus/usb/devices/1-1/idVendor)
        product=$(cat /sys/bus/usb/devices/1-1/idProduct)
        echo "HOTPLUG_CONNECTED cycle=$cycle id=$vendor:$product devnum=$(cat /sys/bus/usb/devices/1-1/devnum)"
        if [ "$vendor:$product" != "0c45:64ab" ]; then echo "HOTPLUG_FAIL wrong_device cycle=$cycle"; return 1; fi
        capture=$(/usr/bin/uvc_probe /dev/video0 1280 720 10 2>&1)
        result=$?
        printf '%s\n' "$capture"
        if [ "$result" -ne 0 ] && [ "$result" -ne 2 ]; then echo "HOTPLUG_FAIL capture_error cycle=$cycle"; return 1; fi
        if ! printf '%s\n' "$capture" | grep -Eq '^CAPTURE_(PASS .*1280x720 frames=10|FRAMES_OK .*negotiated=1280x720 frames=10)'; then echo "HOTPLUG_FAIL no_frame_evidence cycle=$cycle"; return 1; fi
        echo "HOTPLUG_CYCLE_DONE cycle=$cycle requested_30fps_still_unmet"
    done
    echo HOTPLUG_THREE_CYCLES_DONE
}
hotplug_check
