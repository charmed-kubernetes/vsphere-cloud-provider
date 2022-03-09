# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path

from templates import TemplateEngine

DATA_PATH = Path(__file__).parent.parent / "data"


def test_render_all(request):
    templates = TemplateEngine(
        juju_app=request.node.name,
        server="1.2.3.4",
        datacenter="dc1",
        username="alice@vsphere.local",
        password="s3cr3t",
        image="gcr.io/cloud-provider-vsphere/cpi/release/manager:latest",
        control_node_selector={"juju-application": "kubernetes-control-plane"},
    )
    rendered = [
        templates.config_map.yaml,
        templates.provider.yaml,
        templates.role_bindings.yaml,
        templates.roles.yaml,
        templates.secret.yaml,
    ]
    expected = (DATA_PATH / "rendered-templates.yaml").read_text()
    assert "\n".join(rendered) + "\n" == expected
