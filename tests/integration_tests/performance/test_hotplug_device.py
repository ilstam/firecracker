# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Performance test for PCIe device hot-plug.

Measures the guest-visible cost of hot-plugging a block device: from just
before the Firecracker API call until the guest has bound a driver to the
new device. Hot-unplug is measured the same way, until the device is gone
from the guest's PCI bus again.
"""

import os
import time

import pytest

import host_tools.drive as drive_tools
from framework.artifacts import GUEST_KERNEL_DEFAULT, pin_guest_kernel
from framework.properties import global_props

pytestmark = pin_guest_kernel(GUEST_KERNEL_DEFAULT)

# Each iteration gets a fresh microVM so the samples are independent.
ITERATIONS = 20

# Hotplug and unplug timeout in seconds.
TIMEOUT = 10

# The PCI host bridge, which every PCI-enabled microVM has. The uevent
# monitor needs some device that is already there to handshake against.
SYNC_DEVICE = "/sys/bus/pci/devices/0000:00:00.0"

DEVICE_TYPE = "block"
DRIVE_ID = "blk_hotplugged"

METRICS = {
    "auto": "plug_total_time",
    "rescan": "plug_rescan_total_time",
}


def boot_microvm(microvm_factory, guest_kernel, rootfs):
    """Boot a microVM with one free root port to plug into."""
    vm = microvm_factory.build(guest_kernel, rootfs, pci=True)
    vm.spawn()
    vm.memory_monitor = None
    vm.basic_config(pcie_hotplug_ports=1)
    vm.add_net_iface()
    vm.start()
    return vm


def dimensions(vm, test_name):
    """The dimensions identifying one CloudWatch metric stream."""
    return {
        "instance": global_props.instance,
        "cpu_model": global_props.cpu_model,
        "host_kernel": f"linux-{global_props.host_linux_version}",
        "performance_test": test_name,
        "device_type": DEVICE_TYPE,
        **vm.dimensions,
    }


def stage_hotplug(vm):
    """Prepare host resources and return a callable issuing the hot-plug.

    Creating the backing file is host-side setup, not part of the hot-plug,
    so it has to happen before timing starts.
    """
    host_file = drive_tools.FilesystemFile(os.path.join(vm.fsfiles, DRIVE_ID), size=4)
    jailed = vm.create_jailed_resource(host_file.path)

    return lambda: vm.api.drive.put(
        drive_id=DRIVE_ID,
        path_on_host=jailed,
        is_root_device=False,
        is_read_only=False,
    )


def time_hotplug(vm, plug_mode):
    """Milliseconds from the hot-plug request until the guest binds it."""
    issue_hotplug = stage_hotplug(vm)

    # The probe channel only serves the rescan below, but opening it is
    # untimed, so it is not worth making conditional.
    uevents = vm.ssh.uevent_monitor("pci", SYNC_DEVICE, TIMEOUT)
    with uevents as wait_for_uevent, vm.ssh.probe_channel() as probe:
        start = time.perf_counter()
        issue_hotplug()

        if plug_mode == "rescan":
            # Fire and forget: the write does the bus scan and the driver
            # probe inline, so waiting for its reply would put another ssh
            # round trip inside the measurement.
            probe("echo 1 > /sys/bus/pci/rescan", wait=False)

        # `bind` on the PCI device is the last event of the cascade, and
        # means the same thing in both modes, unlike the device merely
        # appearing under /sys/bus/pci/devices: a rescan does the driver
        # probe inline, whereas pciehp defers it.
        wait_for_uevent("bind")
        return (time.perf_counter() - start) * 1000


def time_hotunplug(vm):
    """Milliseconds from the unplug request until the guest drops it."""
    issue_hotplug = stage_hotplug(vm)

    with vm.ssh.uevent_monitor("pci", SYNC_DEVICE, TIMEOUT) as wait_for_uevent:
        # Getting a device onto the bus is setup, so it is not timed.
        issue_hotplug()
        wait_for_uevent("bind")

        start = time.perf_counter()
        vm.api.drive.delete(DRIVE_ID)
        # The guest unbinds the driver first and removes the device last.
        wait_for_uevent("remove")
        return (time.perf_counter() - start) * 1000


@pytest.mark.nonci
@pytest.mark.parametrize("plug_mode", sorted(METRICS))
def test_hotplug_device_latency(
    microvm_factory, guest_kernel, rootfs, plug_mode, metrics
):
    """Measure how long a hot-plugged device takes to reach the guest."""
    for i in range(ITERATIONS):
        vm = boot_microvm(microvm_factory, guest_kernel, rootfs)

        if i == 0:
            metrics.set_dimensions(dimensions(vm, "test_hotplug_device_latency"))

        metrics.put_metric(
            METRICS[plug_mode], time_hotplug(vm, plug_mode), unit="Milliseconds"
        )

        vm.kill()


@pytest.mark.nonci
def test_hotunplug_device_latency(microvm_factory, guest_kernel, rootfs, metrics):
    """Measure how long a hot-unplugged device takes to leave the guest."""
    for i in range(ITERATIONS):
        vm = boot_microvm(microvm_factory, guest_kernel, rootfs)

        if i == 0:
            metrics.set_dimensions(dimensions(vm, "test_hotunplug_device_latency"))

        metrics.put_metric("unplug_total_time", time_hotunplug(vm), unit="Milliseconds")

        vm.kill()
