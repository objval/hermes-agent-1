"""Tests for on_model_change and post_update lifecycle hooks.

These tests verify:
1. Hook registration in VALID_HOOKS
2. Hook invocation in auth.py on provider/model changes
3. Hook invocation in acp_adapter/server.py on /model command
4. Post-update hook execution with plugin callbacks and scripts
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from hermes_cli.plugins import VALID_HOOKS


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
        from hermes_cli.auth import _update_config_for_provider
        
        # Create a temp config file
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            
            # Write initial config with openai provider
            config_path.write_text("model:\n  provider: openai\n  default: gpt-4\n")
            
            with patch("hermes_cli.auth.get_config_path", return_value=config_path):
                # Switch to anthropic provider
                _update_config_for_provider("anthropic", "", default_model="claude-opus-4")
                
                # Verify hook was called with correct provider change
                mock_invoke.assert_called_once()
                call_kwargs = mock_invoke.call_args[1]
                assert call_kwargs["old_provider"] == "openai"
                assert call_kwargs["new_provider"] == "anthropic"

    @patch("hermes_cli.plugins.invoke_hook")
    def test_hook_not_fired_when_no_change(self, mock_invoke):
        """Hook should NOT fire when provider/model unchanged."""
        from hermes_cli.auth import _save_model_choice

        # Setup: mock config with same model
        with patch("hermes_cli.config.load_config") as mock_load:
            with patch("hermes_cli.config.save_config"):
                mock_load.return_value = {
                    "model": {"provider": "openai", "default": "gpt-4"}
                }
                
                # Save same model (no change)
                _save_model_choice("gpt-4")
                
                # Hook should NOT be called when no actual change
                mock_invoke.assert_not_called()

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
    @patch("subprocess.run")
    def test_script_execution_from_post_update_d(self, mock_subprocess, mock_home):
        """Scripts from post-update.d should be executed with correct env vars."""
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

            # Run scripts
            executed = _run_post_update_scripts(
                update_status="success",
                prev_version="abc",
                new_version="def",
                commits_count=1
            )
            
            # Verify subprocess.run was called with correct args
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args
            assert call_args[0][0][0] == "bash"
            assert "01-test.sh" in call_args[0][0][1]
            assert executed == 1
            
            # Verify environment variables were set
            env = call_args[1]["env"]
            assert env["HERMES_UPDATE_STATUS"] == "success"
            assert env["HERMES_PREV_VERSION"] == "abc"
            assert env["HERMES_NEW_VERSION"] == "def"
            assert env["HERMES_COMMITS_COUNT"] == "1"

    @patch("hermes_cli.profiles._get_default_hermes_home")
    @patch("subprocess.run", side_effect=FileNotFoundError("node"))
    def test_script_execution_count_skips_failed_launch(self, _mock_subprocess, mock_home):
        """Failed interpreter launch should not count as executed script."""
        from hermes_cli.main import _run_post_update_scripts

        with tempfile.TemporaryDirectory() as tmpdir:
            scripts_dir = Path(tmpdir) / "post-update.d"
            scripts_dir.mkdir()
            mock_home.return_value = Path(tmpdir)

            script = scripts_dir / "01-test.js"
            script.write_text("console.log('test')")
            script.chmod(0o755)

            executed = _run_post_update_scripts(
                update_status="success",
                prev_version="abc",
                new_version="def",
                commits_count=1,
            )

            assert executed == 0

    @patch("hermes_cli.profiles._get_default_hermes_home")
    def test_non_executable_scripts_skipped(self, mock_home):
        """Scripts without executable bit should be skipped."""
        from hermes_cli.main import _run_post_update_scripts

        with tempfile.TemporaryDirectory() as tmpdir:
            scripts_dir = Path(tmpdir) / "post-update.d"
            scripts_dir.mkdir()
            mock_home.return_value = Path(tmpdir)

            # Create a non-executable script
            script = scripts_dir / "01-test.sh"
            script.write_text("#!/bin/bash\necho 'Test'")
            # No chmod - not executable
            
            with patch("subprocess.run") as mock_run:
                _run_post_update_scripts(
                    update_status="success",
                    prev_version="abc",
                    new_version="def",
                    commits_count=1
                )
                # subprocess.run should NOT be called for non-executable script
                mock_run.assert_not_called()


class TestPostUpdateScriptsEnvironment:
    """Test that scripts receive correct environment variables."""

    @patch("hermes_cli.profiles._get_default_hermes_home")
    @patch("subprocess.run")
    def test_environment_variables_propagated(self, mock_subprocess, mock_home):
        """Scripts should receive HERMES_UPDATE_STATUS, HERMES_PREV_VERSION, etc."""
        from hermes_cli.main import _run_post_update_scripts

        with tempfile.TemporaryDirectory() as tmpdir:
            scripts_dir = Path(tmpdir) / "post-update.d"
            scripts_dir.mkdir()
            mock_home.return_value = Path(tmpdir)

            script = scripts_dir / "01-test.sh"
            script.write_text("#!/bin/bash\necho 'test'")
            script.chmod(0o755)

            _run_post_update_scripts(
                update_status="failed",
                prev_version="old-sha",
                new_version="new-sha",
                commits_count=3
            )

            # Get the environment passed to subprocess
            env = mock_subprocess.call_args[1]["env"]
            
            # Verify all expected env vars are present
            assert env["HERMES_UPDATE_STATUS"] == "failed"
            assert env["HERMES_PREV_VERSION"] == "old-sha"
            assert env["HERMES_NEW_VERSION"] == "new-sha"
            assert env["HERMES_COMMITS_COUNT"] == "3"
            assert "HERMES_HOME" in env
            assert "HERMES_SCRIPTS_DIR" in env


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


class TestGatewayModelChangeHook:
    """Test on_model_change hook in gateway /model command."""

    @patch("hermes_cli.plugins.invoke_hook")
    def test_hook_not_fired_when_no_change_gateway(self, mock_invoke):
        """Hook should NOT fire in gateway when model/provider unchanged."""
        from acp_adapter.server import HermesACPAgent
        
        # Create mock session state
        state = MagicMock()
        state.model = "gpt-4"
        state.agent = MagicMock()
        state.agent.model = "gpt-4"
        state.agent.provider = "openai"
        state.session_id = "test-session"
        state.cwd = "/tmp"
        
        # Create mock session manager
        session_manager = MagicMock()
        
        # Create handler and call _cmd_model with same model
        handler = HermesACPAgent.__new__(HermesACPAgent)
        handler.session_manager = session_manager
        
        # Call with same model (no change)
        result = handler._cmd_model("gpt-4", state)
        
        # Hook should NOT be called when no actual change
        mock_invoke.assert_not_called()

    @patch("hermes_cli.plugins.invoke_hook")
    def test_hook_fired_on_model_change_gateway(self, mock_invoke):
        """Hook should fire in gateway when model changes."""
        from acp_adapter.server import HermesACPAgent
        
        # Create mock session state with openai
        state = MagicMock()
        state.model = "gpt-4"
        state.agent = MagicMock()
        state.agent.model = "gpt-4"
        state.agent.provider = "openai"
        state.session_id = "test-session"
        state.cwd = "/tmp"
        
        # Create mock session manager
        session_manager = MagicMock()
        new_agent = MagicMock()
        new_agent.provider = "anthropic"
        session_manager._make_agent.return_value = new_agent
        
        # Create handler
        handler = HermesACPAgent.__new__(HermesACPAgent)
        handler.session_manager = session_manager
        
        # Mock parse_model_input to return different provider
        with patch("hermes_cli.models.parse_model_input", return_value=("anthropic", "claude-opus-4")):
            with patch("hermes_cli.models.detect_provider_for_model", return_value=None):
                # Call with different model
                result = handler._cmd_model("claude-opus-4", state)
                
                # Hook should be called
                mock_invoke.assert_called_once()
                call_kwargs = mock_invoke.call_args[1]
                assert call_kwargs["old_model"] == "gpt-4"
                assert call_kwargs["new_model"] == "claude-opus-4"
                assert call_kwargs["old_provider"] == "openai"
                assert call_kwargs["new_provider"] == "anthropic"

    @patch("hermes_cli.plugins.invoke_hook")
    def test_gateway_hook_normalizes_values_to_strings(self, mock_invoke):
        """Gateway hook payload should always use non-empty strings."""
        from acp_adapter.server import HermesACPAgent

        state = MagicMock()
        state.model = ""  # falsy state.model
        state.agent = MagicMock()
        state.agent.model = None
        state.agent.provider = None
        state.session_id = "test-session"
        state.cwd = "/tmp"

        session_manager = MagicMock()
        new_agent = MagicMock()
        new_agent.provider = ""  # force fallback to target/current provider
        session_manager._make_agent.return_value = new_agent

        handler = HermesACPAgent.__new__(HermesACPAgent)
        handler.session_manager = session_manager

        with patch("hermes_cli.models.parse_model_input", return_value=("anthropic", "claude-opus-4")):
            with patch("hermes_cli.models.detect_provider_for_model", return_value=None):
                handler._cmd_model("claude-opus-4", state)

        mock_invoke.assert_called_once()
        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["old_model"] == "unknown"
        assert call_kwargs["new_model"] == "claude-opus-4"
        assert call_kwargs["old_provider"] == "openrouter"
        assert call_kwargs["new_provider"] == "anthropic"
        assert all(isinstance(call_kwargs[k], str) for k in ["old_model", "new_model", "old_provider", "new_provider"])
        assert all(call_kwargs[k] for k in ["old_model", "new_model", "old_provider", "new_provider"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
