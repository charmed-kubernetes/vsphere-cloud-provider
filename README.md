# vsphere-cloud-provider

## Description

This subordinate charm manages the Cloud Provider component of the vSphere.

## Usage

The charm requires vSphere credentials and connection information, which
can be provided either directly, via config, or via the `vsphere-integration`
relation to the [vSphere Integrator charm](https://charmhub.io/vsphere-integrator).

## Deployment

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

kubectl taint nodes -l juju-application=kubernetes-worker node.cloudprovider.kubernetes.io/uninitialized=true:NoSchedule
kubectl taint nodes -l juju-application=kubernetes-control-plane node.cloudprovider.kubernetes.io/uninitialized=true:NoSchedule
kubectl describe nodes |egrep "Taints:|Name:|Provider"
```

### Details

* Requires a `charmed-kubernetes` deployment on a vsphere cloud launched by juju
* Deploy the `vsphere-integrator` charm into the model using `--trust` so juju provided vsphere credentials
* Deploy the `vsphere-cloud-provider` charm in the model relating to the integrator and to charmed-kubernetes compnents
* Once the model is active/idle, the cloud-provider charm will have successfully deployed the vsphere controller in the kube-system
  namespace
* Taint the existing nodes so the controller will apply the correct provider id to those nodes. 
* Confirm the `ProviderID` is set on each node

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](https://github.com/canonical/vsphere-cloud-provider/blob/main/CONTRIBUTING.md)
for developer guidance.
