# CI 失败报告

- run id: 45786910589
- commit: 363dba12d5b2e3a07c9582fafe578bc86cd3e88e

========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_startup_fails_without_secret
--- message ---
ModuleNotFoundError: No module named 'jsonschema'
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:101: in test_startup_fails_without_secret
    _load_agent_main(package_root=target, alias="_agent_svc_tmp")
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
/tmp/pytest-of-runner/pytest-0/test_startup_fails_without_sec0/svc/app/main.py:24: in <module>
    from .agents import build_agents
/tmp/pytest-of-runner/pytest-0/test_startup_fails_without_sec0/svc/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
/tmp/pytest-of-runner/pytest-0/test_startup_fails_without_sec0/svc/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
/tmp/pytest-of-runner/pytest-0/test_startup_fails_without_sec0/svc/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_startup_allows_explicit_insecure_escape_hatch
--- message ---
ModuleNotFoundError: No module named 'jsonschema'
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:121: in test_startup_allows_explicit_insecure_escape_hatch
    module = _load_agent_main(package_root=target, alias="_agent_svc_tmp")
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
/tmp/pytest-of-runner/pytest-0/test_startup_allows_explicit_i0/svc/app/main.py:24: in <module>
    from .agents import build_agents
/tmp/pytest-of-runner/pytest-0/test_startup_allows_explicit_i0/svc/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
/tmp/pytest-of-runner/pytest-0/test_startup_allows_explicit_i0/svc/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
/tmp/pytest-of-runner/pytest-0/test_startup_allows_explicit_i0/svc/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_all_mutating_endpoints_require_auth[/v1/agent/draft-draft]
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_all_mutating_endpoints_require_auth[/v1/agent/rating-rating]
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_all_mutating_endpoints_require_auth[/v1/agent/evidence-check-evidence]
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_all_mutating_endpoints_require_auth[/v1/agent/reload-None]
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_auth_is_checked_before_body_validation
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_valid_token_is_accepted
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_wrong_token_is_rejected
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_malformed_authorization_header_is_rejected
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_health_endpoint_stays_public
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_agent_service_auth_r12::test_error_message_does_not_leak_secret
--- message ---
failed on setup with "ModuleNotFoundError: No module named 'jsonschema'"
--- traceback ---
backend/app/tests/test_agent_service_auth_r12.py:81: in agent_app
    module = _load_agent_main()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_agent_service_auth_r12.py:68: in _load_agent_main
    main = importlib.import_module(f"{alias}.main")
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
/opt/hostedtoolcache/Python/3.12.16/x64/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
<frozen importlib._bootstrap>:1387: in _gcd_import
    ???
<frozen importlib._bootstrap>:1360: in _find_and_load
    ???
<frozen importlib._bootstrap>:1331: in _find_and_load_unlocked
    ???
<frozen importlib._bootstrap>:935: in _load_unlocked
    ???
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/main.py:24: in <module>
    from .agents import build_agents
agent-service/app/agents.py:27: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
========================================================================
FAILED: backend.app.tests.test_plan_hallucination_detector::test_plan_agent_prompt_forbids_assessment_numbers
--- message ---
ModuleNotFoundError: No module named 'jsonschema'
--- traceback ---
backend/app/tests/test_plan_hallucination_detector.py:224: in test_plan_agent_prompt_forbids_assessment_numbers
    module = _load_plan_agent()
             ^^^^^^^^^^^^^^^^^^
backend/app/tests/test_plan_hallucination_detector.py:210: in _load_plan_agent
    module_spec.loader.exec_module(module)
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
agent-service/app/plan_agent.py:49: in <module>
    from .tool_agent import ToolUsingAgent
agent-service/app/tool_agent.py:13: in <module>
    from .tools import ToolRegistry
agent-service/app/tools.py:16: in <module>
    from jsonschema import Draft202012Validator
E   ModuleNotFoundError: No module named 'jsonschema'
