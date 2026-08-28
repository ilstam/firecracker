# Device Hotplugging [Developer Preview]

> [!WARNING]
>
> This feature is currently in
> [Developer Preview](RELEASE_POLICY.md#developer-preview-features). It may have
> limitations, and its API or behavior may change in future releases.

Device hotplugging allows attaching and detaching PCI virtio devices to a
running microVM without requiring a reboot. Supported device types are:

- `virtio-block`
- `virtio-pmem`
- `virtio-net`

## Prerequisites

- **PCI transport enabled**: Firecracker must be started with the `--enable-pci`
  flag. Device hotplugging is not supported with MMIO transport.
- **PCIe hot-plug ports configured**: `pcie_hotplug_ports` must be set in the
  machine configuration. It defaults to 0, which disables hotplugging.
- **Guest kernel with PCI and PCIe hot-plug support**: The guest kernel must
  have PCI, the relevant virtio drivers, and the native PCI Express hot-plug
  driver (`pciehp`) enabled (`CONFIG_HOTPLUG_PCI=y`,
  `CONFIG_HOTPLUG_PCI_PCIE=y`, `CONFIG_PCIEPORTBUS=y`). See the
  [kernel policy documentation](kernel-policy.md) for details.

## How it works

Firecracker puts a number of PCI Express Root Ports on the root bus, each with
one hot-plug capable slot. A hotplugged device goes into a free slot, and the
Root Port raises a native PCI Express hot-plug interrupt. The guest's `pciehp`
driver handles it and binds or unbinds the device by itself, so no PCI bus
rescan or manual device removal is needed inside the guest.

Each Root Port occupies one slot on the root bus and starts one secondary bus,
which is where its device appears. The ports take the slots after the boot
devices, so adding ports does not move any boot device or change the name the
guest gives it.

The bus numbers are assigned by Firecracker and the guest is expected to keep
them rather than renumbering the bridges. Linux does keep them: it only
reprograms a bridge's bus numbers when asked to reassign all buses, which
neither an ACPI platform (x86_64) nor a device-tree `pci-host-ecam-generic` host
bridge (aarch64) requests. A guest that forces renumbering, for example Linux
booted with `pci=assign-busses`, is not supported.

## Reserving hot-plug ports

Ports are reserved at boot through the `pcie_hotplug_ports` machine
configuration option, which is the maximum number of devices that can be
hotplugged at any one time. It cannot be changed after boot.

```console
socket_location=/run/firecracker.socket

curl --unix-socket $socket_location -i \
    -X PUT 'http://localhost/machine-config' \
    -H 'Content-Type: application/json' \
    -d '{
        "vcpu_count": 2,
        "mem_size_mib": 1024,
        "pcie_hotplug_ports": 4
    }'
```

Each port costs a small amount of guest address space and one MSI-X vector, and
makes the guest enumerate one more bus at boot, so reserve what you expect to
need rather than the maximum. The maximum is 31.

## Hotplugging a device

Hotplugging uses the same API endpoints used for pre-boot device configuration.
The only difference is that the request is issued after the VM has started.

```console
curl --unix-socket $socket_location -i \
    -X PUT 'http://localhost/drives/block1' \
    -H 'Accept: application/json' \
    -H 'Content-Type: application/json' \
    -d '{
        "drive_id": "block1",
        "path_on_host": "/path/to/block.ext4",
        "is_root_device": false,
        "is_read_only": false
    }'
```

The guest discovers the device by itself. Shortly after the call returns the
device appears in `lspci` and its device node (for example `/dev/vdb` or
`/dev/pmem1`) is created, with nothing to do inside the guest.

The request fails if every reserved port is already occupied. Unplug a device
first, or start the VM with a larger `pcie_hotplug_ports`.

## Making a boot device removable

A device configured before boot normally sits on the root bus, where the guest
has no way of being told it is going away, so it cannot be unplugged. Setting
`removable` on it puts it in a Root Port slot instead. It is present from boot
as usual, but can be hot-unplugged later.

```console
curl --unix-socket $socket_location -i \
    -X PUT 'http://localhost/drives/scratch' \
    -H 'Content-Type: application/json' \
    -d '{
        "drive_id": "scratch",
        "path_on_host": "/path/to/scratch.ext4",
        "is_root_device": false,
        "is_read_only": false,
        "removable": true
    }'
```

Each removable device consumes one of the ports reserved by
`pcie_hotplug_ports`, so it is unavailable for hotplugging later.

Note that a removable device is on a secondary bus rather than the root bus,
which changes its guest-visible PCI address. For a network interface that also
changes the name the guest derives from it, from an `enp0sN` form to an `enp1s0`
one. The option has no effect on a hotplugged device, which always goes behind a
Root Port.

## Hot-unplugging a device

Issue a `DELETE` request to the device's endpoint:

```console
curl --unix-socket $socket_location -i \
    -X DELETE 'http://localhost/drives/block1'
```

This starts a *managed* removal. Firecracker asks the guest to release the
device by pressing the Root Port's attention button, and the call returns
immediately, before the device is gone. The guest's `pciehp` driver then
quiesces the device and powers the slot off in its own time, and Firecracker
frees the backing resources at that point. Linux waits five seconds after the
button press before acting, so that a second press can cancel the removal.

Poll `GET /vm/config` to find out when the device has actually gone.

> [!NOTE]
>
> A managed removal quiesces the guest *driver*, but it does not flush
> application state. For data safety, stop using the device before unplugging it
> — for example unmount any mounted filesystem or bring down a network interface
> first:
>
> ```bash
> umount /mnt/block1        # for block / pmem devices
> ip link set eth1 down     # for network devices
> ```

Only a device behind a Root Port can be unplugged, that is a hotplugged device
or one marked `removable`. Unplugging anything else is rejected, as is
unplugging a root block or pmem device.

### Forcing a removal

If the guest does not respond, `force` removes the device immediately without
waiting:

```console
curl --unix-socket $socket_location -i \
    -X DELETE 'http://localhost/drives/block1' \
    -H 'Content-Type: application/json' \
    -d '{ "force": true }'
```

The guest is told the slot is empty only after the fact, so a driver may still
be holding the device and in-flight I/O can be lost. Use it as an escape hatch,
not as the normal path.

## Snapshots

Reserved ports and the devices in them are part of a snapshot. A restored VM has
the same ports at the same addresses, so devices hotplugged before the snapshot
keep working, they can still be unplugged, and free ports can still be filled.

`GET /vm/config` on a restored VM reports `removable` for every device behind a
Root Port, including the ones that got there by being hotplugged.
