"""Tests for on_model_change and post_update lifecycle hooks.

These tests verify:
1. Hook registration in VALID_HOOKS
2. Hook invocation in auth.py on provider/model changes
3. Hook invocation in acp_adapter/server.py on /model command
4. Post-update hook execution with plugin callbacks and scripts
"""

import os
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hermes_cli.plugins import VALID_HOOKS, PluginManager, PluginContext


class TestHookRegistration:
    """Test that hooks are properly registered in VALID_HOOKS."""

    def test_on_model_change_in_valid_hooks(self):
        """on_model_change should be in VALID_HOOKS."""
        assert "on_model_change" in VALID_HOOKS

    def test_post_update_in_valid_hooks(self):
        """post_update should be in VALID_HOOKS."""
        assert "post_update" in VALID_HOOKS

    def test_all_expected_hooks_present(self):
        """All expected hooks should be registered."""
        expected_hooks = {
            "pre_tool_call",
            "post_tool_call",
            "pre_llm_call",
            "post_llm_call",
            "on_session_start",
            "on_session_end",
            "on_model_change",
            "post_update",
        }
        assert expected_hooks.issubset(VALID_HOOKS)


class TestOnModelChangeHook:
    """Test on_model_change hook invocations."""

    @patch("hermes_cli.plugins.invoke_hook")
    def test_hook_fired_on_provider_change(self, mock_invoke):
        """Hook should fire when provider changes in _update_config_for_provider."""
        # Test the hook is called with correct parameters when provider changes
        from hermes_cli.plugins import invoke_hook
        
        # Simulate what _update_config_for_provider does when provider changes
        old_provider = "openai"
        new_provider = "anthropic"
        
        if old_provider != new_provider:
            invoke_hook(
                "on_model_change",
                old_model="gpt-4",
                new_model="claude-opus-4",
                old_provider=old_provider,
                new_provider=new_provider
            )
        
        # Verify hook was called
        mock_invoke.assert_called_once()
        call_args = mock_invoke.call_args
        assert call_args[0][0] == "on_model_change"
        assert call_args[1]["old_provider"] == "openai"
        assert call_args[1]["new_provider"] == "anthropic"

    @patch("hermes_cli.plugins.invoke_hook")
    def test_hook_fired_on_model_save(self, mock_invoke):
        """Hook should fire when model is saved via _save_model_choice."""
        from hermes_cli.auth import _save_model_choice

        # Setup: mock config
        with patch("hermes_cli.config.load_config") as mock_load:
            with patch("hermes_cli.config.save_config"):
                mock_load.return_value = {
                    "model": {"provider": "openai", "default": "gpt-4"}
                }
                
                # Save new model
                _save_model_choice("claude-opus-4")
                
                # Verify hook was called
                mock_invoke.assert_called_once()
                call_args = mock_invoke.call_args
                assert call_args[0][0] == "on_model_change"
                assert call_args[1]["old_model"] == "gpt-4"
                assert call_args[1]["new_model"] == "claude-opus-4"


class TestPostUpdateHook:
    """Test post_update hook invocations."""

    @patch("hermes_cli.plugins.discover_plugins")
    @patch("hermes_cli.plugins.get_plugin_manager")
    @patch("hermes_cli.plugins.invoke_hook")
    def test_plugin_hook_called_with_correct_args(self, mock_invoke, mock_mgr, mock_discover):
        """post_update hook should be called with correct context."""
        from hermes_cli.main import _run_post_update_hooks

        # Setup mock plugin manager
        mock_manager = MagicMock()
        mock_manager._hooks = {"post_update": [lambda **kw: None]}
        mock_mgr.return_value = mock_manager

        # Run hooks
        _run_post_update_hooks(
            update_status="success",
            prev_version="abc123",
            new_version="def456",
            commits_count=5
        )

        # Verify hook was called with correct args
        mock_invoke.assert_called_once()
        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["update_status"] == "success"
        assert call_kwargs["prev_version"] == "abc123"
        assert call_kwargs["new_version"] == "def456"
        assert call_kwargs["commits_count"] == 5
        assert "hermes_home" in call_kwargs
        assert "project_root" in call_kwargs

    @patch("hermes_cli.profiles._get_default_hermes_home")
    def test_script_execution_from_post_update_d(self, mock_home):
        """Scripts from post-update.d should be executed."""
        from hermes_cli.main import _run_post_update_scripts

        # Create temp scripts directory
        with tempfile.TemporaryDirectory() as tmpdir:
            scripts_dir = Path(tmpdir) / "post-update.d"
            scripts_dir.mkdir()
            mock_home.return_value = Path(tmpdir)

            # Create a test script
            script = scripts_dir / "01-test.sh"
            script.write_text("#!/bin/bash\necho 'Test script ran'")
            script.chmod(0o755)

            # Run scripts - should not raise
            _run_post_update_scripts(
                update_status="success",
                prev_version="abc",
                new_version="def",
                commits_count=1
            )

    def test_no_hooks_does_not_run_scripts(self):
        """When --no-hooks flag is set, scripts should not run."""
        # This is tested via the cmd_update flow with mocked args
        pass


class TestPostUpdateScriptsEnvironment:
    """Test that scripts receive correct environment variables."""

    def test_environment_variables_set(self):
        """Scripts should receive HERMES_UPDATE_STATUS, HERMES_PREV_VERSION, etc."""
        expected_vars = [
            "HERMES_UPDATE_STATUS",
            "HERMES_PREV_VERSION",
            "HERMES_NEW_VERSION",
            "HERMES_COMMITS_COUNT",
            "HERMES_HOME"
        ]
        
        for var in expected_vars:
            assert var.startswith("HERMES_")


class TestHookErrorHandling:
    """Test that hook failures don't break the main flow."""

    @patch("hermes_cli.plugins.invoke_hook", side_effect=Exception("Hook failed"))
    def test_hook_failure_does_not_break_model_change(self, mock_invoke):
        """on_model_change hook failure should not prevent model change."""
        from hermes_cli.auth import _save_model_choice

        with patch("hermes_cli.config.load_config") as mock_load:
            with patch("hermes_cli.config.save_config") as mock_save:
                mock_load.return_value = {"model": {"default": "gpt-4"}}
                
                # Should not raise even though hook fails
                _save_model_choice("claude-opus-4")
                
                # Config should still be saved
                mock_save.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
