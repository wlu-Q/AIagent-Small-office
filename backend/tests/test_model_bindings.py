import pytest

from app.gateway import model_bindings
from app.gateway.model_bindings import ModelBindingError, resolve_run_app_config
from deerflow.config.app_config import AppConfig


def _base_config(*models: dict[str, str]) -> AppConfig:
    return AppConfig.model_validate(
        {
            'sandbox': {'use': 'deerflow.sandbox.local:LocalSandboxProvider'},
            'models': list(models) or [
                {
                    'name': 'default',
                    'use': 'langchain_openai:ChatOpenAI',
                    'model': 'default-model',
                    'api_key': 'default-secret',
                },
            ],
        }
    )


def test_resolve_run_app_config_uses_only_selected_deerflow_profile():
    base = _base_config(
        {
            'name': 'profile-1',
            'use': 'deerflow.models.patched_mimo:PatchedChatMiMo',
            'model': 'mimo-v2.5',
            'api_key': 'run-secret',
            'base_url': 'https://example.com/v1/',
        },
    )
    resolved = resolve_run_app_config(base, 'profile-1')

    assert resolved is not base
    assert len(resolved.models) == 1
    model = resolved.models[0]
    assert model.name == 'office-selected-model'
    assert model.model == 'mimo-v2.5'
    assert model.use == 'deerflow.models.patched_mimo:PatchedChatMiMo'
    assert model.model_extra['api_key'] == 'run-secret'
    assert model.model_extra['base_url'] == 'https://example.com/v1'
    assert (
        model_bindings.resolve_runtime_model_id(
            resolved,
            model_bindings.SELECTED_MODEL_NAME,
        )
        == 'mimo-v2.5'
    )


def test_resolve_run_app_config_adds_employee_profiles_for_office_subagents():
    resolved = resolve_run_app_config(
        _base_config(
            {
                'name': 'pm-profile',
                'use': 'langchain_openai:ChatOpenAI',
                'model': 'pm-model',
                'api_key': 'pm-secret',
                'base_url': 'https://pm.example.com/v1',
            },
            {
                'name': 'worker-profile',
                'use': 'langchain_openai:ChatOpenAI',
                'model': 'worker-model',
                'api_key': 'worker-secret',
                'base_url': 'https://worker.example.com/v1',
            },
        ),
        'pm-profile',
        additional_profile_ids=['worker-profile', 'pm-profile'],
    )

    assert [model.name for model in resolved.models] == [
        'office-selected-model',
        'worker-profile',
        'pm-profile',
    ]
    assert resolved.get_model_config('worker-profile').model == 'worker-model'
    assert resolved.get_model_config('pm-profile').model == 'pm-model'


@pytest.mark.parametrize('profile_id', ['', 'missing'])
def test_resolve_run_app_config_never_falls_back_to_default(profile_id):

    with pytest.raises(ModelBindingError):
        resolve_run_app_config(_base_config(), profile_id)


def test_model_profile_exists_uses_the_same_deerflow_configuration_as_run_resolution():
    base = _base_config(
        {
            'name': 'mimo-stage7',
            'use': 'deerflow.models.patched_mimo:PatchedChatMiMo',
            'model': 'mimo-v2.5',
            'api_key': 'run-secret',
            'base_url': 'https://example.com/v1',
        },
    )

    assert model_bindings.model_profile_exists(base, 'mimo-stage7') is True
    assert model_bindings.model_profile_exists(base, 'missing') is False
