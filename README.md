# vsphere-cloud-provider

## Description

This subordinate charm manages the cloud-provider and vsphere-csi-driver components in vSphere.

## Requirements
* vSphere infrastructure must support version 15+ VMs (tested on version 17)
   * See [vmware compatibility docs](https://docs.vmware.com/en/VMware-vSphere/6.7/com.vmware.vsphere.vm_admin.doc/GUID-789C3913-1053-4850-A0F0-E29C3D32B6DA.html)

### Component compatibility

The defaults target Charmed Kubernetes 1.36: CPI `v1.36` (the upstream release
directory, containing image `v1.36.0`) and CSI `v3.8.0`. CPI compatibility is
[guaranteed only for the matching Kubernetes minor](https://github.com/kubernetes/cloud-provider-vsphere/blob/v1.36.0/README.md#compatibility-with-kubernetes).
CSI `v3.8.0` [supports Kubernetes 1.34 through 1.36](https://github.com/kubernetes-sigs/vsphere-csi-driver/releases/tag/v3.8.0).
Other bundled releases remain available for explicitly configured deployments;
being bundled does not imply support on Kubernetes 1.36.

Check the [vendor vSphere compatibility matrix](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/container-storage-plugin/3-0/getting-started-with-vmware-vsphere-container-storage-plug-in-3-0/vsphere-container-storage-plug-in-concepts/compatibility-matrix-for-vsphere-container-storage-plug-in.html)
and the prerequisites for the storage features you use. Its minimum recommended
CSI versions include 3.6.0 for vSphere 9.0, 3.3.0 for 8.0 Update 3, 3.0.0 for
8.0 Update 2, and 2.4 for 7.0 Update 3. The vendor documents backward/forward
vSphere compatibility and upgrades from lower to higher CSI versions, but its
published matrix does not yet list CSI 3.8.0 explicitly. Confirm the exact
vCenter/ESX release and feature combination before production rollout; VM
hardware version alone is not sufficient compatibility evidence.

For upgrades, follow the [vendor CSI upgrade guidance](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/container-storage-plugin/3-0/getting-started-with-vmware-vsphere-container-storage-plug-in-3-0/vsphere-container-storage-plug-in-deployment/upgrading-vsphere-container-storage-plug-in.html),
including required vSphere privileges and topology/storage-class considerations.
Pin both `provider-release` and `storage-release` before upgrading the charm on
an older Kubernetes cluster; otherwise the new defaults take effect. Move the
component pins in step with a supported Kubernetes upgrade, and validate both
existing-volume I/O and new-volume provisioning. Kubernetes 1.33 and older are
outside the new CSI default's supported range, so do not adopt that default
before reaching a supported Kubernetes version.

## Usage

The charm requires vSphere credentials and connection information, which
can be provided either directly, via config, or via the `vsphere-integration`
relation to the [vSphere Integrator charm](https://charmhub.io/vsphere-integrator).

## Deployment

### Quickstart
The vSphere Cloud Provider subordinate charm can be deployed alongside Charmed Kubernetes using the overlay provided in the [Charmed Kubernetes bundle repository](https://github.com/charmed-kubernetes/bundle/blob/main/overlays/vsphere-overlay.yaml):
```bash
juju deploy charmed-kubernetes --overlay vsphere-overlay.yaml
```

### The full process

```bash
juju deploy charmed-kubernetes
juju deploy vsphere-integrator --trust
juju deploy vsphere-cloud-provider

juju relate vsphere-cloud-provider:certificates            easyrsa
juju relate vsphere-cloud-provider:kube-control            kubernetes-control-plane
juju relate vsphere-cloud-provider:external-cloud-provider kubernetes-control-plane
juju relate vsphere-cloud-provider                         vsphere-integrator

##  wait for the vsphere controller daemonset to be running
kubectl describe nodes |egrep "Taints:|Name:|Provider"
```

### Details

* Requires a `charmed-kubernetes` deployment on a vsphere cloud launched by juju with the `allow-privileged` flag enabled.
* Deploy the `vsphere-integrator` charm into the model with `--trust` to use juju provided vsphere credentials.
* Deploy the `vsphere-cloud-provider` charm and relate to the integrator and charmed-kubernetes components.
* Once the model is active/idle, the cloud-provider charm will have successfully deployed the vsphere controller in the kube-system
  namespace
* Taint the existing nodes so the controller will apply the correct provider id to those nodes. 
* Confirm the `ProviderID` is set on each node

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](https://github.com/canonical/vsphere-cloud-provider/blob/main/CONTRIBUTING.md)
for developer guidance.
