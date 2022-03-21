# vsphere-cloud-provider-operator

## Description

This charmed operator manages the Cloud Provider component of the vSphere.

## Usage

The charm requires vSphere credentials and connection information, which
can be provided either directly, via config, or via the `vsphere-integration`
relation to the [vSphere Integrator charm](https://charmhub.io/vsphere-integrator).

```bash
juju offer ${CLUSTER_MODEL}.vsphere-integrator:clients
juju consume ${CLUSTER_MODEL}.vsphere-integrator
juju deploy vsphere-cloud-provider-operator --trust
juju relate vsphere-cloud-provider-operator vsphere-integrator
```

You must also tell the cluster on which it is deployed that it will be
acting as an external cloud provider. For Charmed Kubernetes, you can
simply relate it to the control plane.

```bash
juju offer vsphere-cloud-provider-operator:external-cloud-provider
juju switch ${CLUSTER_MODEL}
juju consume ${K8S_MODEL}.vsphere-cloud-provider-operator
juju relate kubernetes-control-plane vsphere-cloud-provider-operator
```

For MicroK8s, you will need to manually modified the config for the following
services to set `cloud-provider=external`, as described in the MicroK8s
documentation under [Configuring Services](https://microk8s.io/docs/configuring-services):

  * `snap.microk8s.daemon-apiserver`
  * `snap.microk8s.daemon-controller-manager`
  * `snap.microk8s.daemon-kubelet`

## OCI Images

The base image for this operator can be provided with `--resource operator-base=ubuntu:focal`.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](https://github.com/canonical/vsphere-cloud-provider-operator/blob/main/CONTRIBUTING.md)
for developer guidance.
