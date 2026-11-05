"""Bind upstream sampling arguments to the actual shared provider transport."""

from dataclasses import replace

from behave.core.records import thaw_json

SAMPLING_POLICIES = ("upstream", "provider_default")


def sampling_policy(value):
    if type(value) is not str or value not in SAMPLING_POLICIES:
        raise ValueError("sampling must be upstream or provider_default")
    return value


def sampling_endpoint(endpoint, sampling, *, provider_sampling="upstream"):
    """Apply the same policy to the saved attempt and each actual model call."""
    policy = sampling_policy(provider_sampling)
    parameters = thaw_json(endpoint.parameters)
    if policy == "upstream":
        parameters.update(sampling)
    else:
        parameters.pop("temperature", None)
        parameters.pop("top_p", None)
    return replace(endpoint, parameters=parameters)


def model_factory(endpoint, *, provider_sampling="upstream"):
    policy = sampling_policy(provider_sampling)

    def build(sampling):
        from behave.agent.process_model import ProcessModelBackend

        configured = sampling_endpoint(endpoint, sampling, provider_sampling=policy)
        return ProcessModelBackend(configured.command()), configured.model_policy()
    return build
