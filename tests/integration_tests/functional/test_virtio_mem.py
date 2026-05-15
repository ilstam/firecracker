# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for virtio-mem device reset."""


def test_virtio_mem_device_reset(uvm_plain_any):
    """
    Test that virtio-mem device reset works.
    """
    vm = uvm_plain_any
    vm.spawn()
    vm.basic_config()
    vm.add_net_iface()
    vm.api.memory_hotplug.put(total_size_mib=256)
    vm.start()

    virtio_dev = vm.ssh.check_output(
        "ls -d /sys/bus/virtio/drivers/virtio_mem/virtio* | xargs -n1 basename"
    ).stdout.strip()

    vm.ssh.check_output(
        f"echo {virtio_dev} > /sys/bus/virtio/drivers/virtio_mem/unbind"
    )

    # Verify the device is gone.
    ret = vm.ssh.run("ls /sys/bus/virtio/drivers/virtio_mem/virtio*")
    assert ret.returncode != 0

    # Rebind and verify it's back.
    vm.ssh.check_output(f"echo {virtio_dev} > /sys/bus/virtio/drivers/virtio_mem/bind")
    vm.ssh.check_output("ls /sys/bus/virtio/drivers/virtio_mem/virtio*")
