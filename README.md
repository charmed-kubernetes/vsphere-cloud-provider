# vsphere-cloud-provider

## Description

This subordinate charm manages the cloud-provider and vsphere-csi-driver components in vSphere.

## Requirements
* vSphere infrastructure must support version 15+ VMs (tested on version 17)
   * See [vmware compatibility docs](https://docs.vmware.com/en/VMware-vSphere/6.7/com.vmware.vsphere.vm_admin.doc/GUID-789C3913-1053-4850-A0F0-E29C3D32B6DA.html)

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
[CONTRIBUTING.md](https://github.com/charmed-kubernetes/vsphere-cloud-provider/blob/main/CONTRIBUTING.md)
for developer guidance.
