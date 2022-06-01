# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import shlex
from pathlib import Path

import pytest
from lightkube.resources.core_v1 import Node

log = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    log.info("Build Charm...")
    charm = await ops_test.build_charm(".")

    overlays = [
        ops_test.Bundle("kubernetes-core", channel="edge"),
        Path("tests/data/charm.yaml"),
    ]

    bundle, *overlays = await ops_test.async_render_bundles(*overlays, charm=charm)

    log.info("Deploy Charm...")
    model = ops_test.model_full_name
    cmd = f"juju deploy -m {model} {bundle} " + " ".join(
        f"--overlay={f} --trust" for f in overlays
    )
    rc, stdout, stderr = await ops_test.run(*shlex.split(cmd))
    assert rc == 0, f"Bundle deploy failed: {(stderr or stdout).strip()}"

    log.info(stdout)
    await ops_test.model.block_until(
        lambda: "vsphere-cloud-provider" in ops_test.model.applications, timeout=60
    )

    await ops_test.model.wait_for_idle(wait_for_active=True, timeout=60 * 60)


async def test_provider_ids(kubernetes):
    nodes = kubernetes.list(Node)
    assert all(node.spec.providerID.startswith("vsphere://") for node in nodes)
