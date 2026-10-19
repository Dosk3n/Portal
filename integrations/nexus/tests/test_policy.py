"""Read-only tool policy must not accidentally permit publication."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_read_only_policy_blocks_report_and_share_changes(monkeypatch):
    from nexus.tool_catalog import get_catalog
    from nexus.tool_policy import ToolAccessError, ToolPolicy
    from nexus.tool_registry import ensure_tool_loaded

    monkeypatch.setenv('NEXUS_TOOL_PACKAGES', 'nexus_tools_portal')
    get_catalog(refresh=True)
    policy = ToolPolicy(mode='restricted', allowed_classes=frozenset({'read'}))
    assert ensure_tool_loaded('portal.check_connection', policy=policy).tool_class == 'read'
    assert ensure_tool_loaded('portal.get_report', policy=policy).tool_class == 'read'
    for name in ('create_report', 'update_report', 'edit_report_details',
                 'restore_version', 'create_share', 'update_share', 'revoke_share'):
        with pytest.raises(ToolAccessError):
            ensure_tool_loaded('portal.' + name, policy=policy)
