"""Closing what the service layer keeps open for the life of the process."""

from . import input_source_service, llm_service, metadata_service


async def close_services() -> None:
    """
    Close the HTTP clients of the default instances, where one was built.

    For the shutdown of the app, which reached for the modules' singletons
    itself (audit 14.11., Q-18). Instances a request built for a provider or
    model of its own are closed by that request.
    """
    default_llm = llm_service._llm_service
    if default_llm:
        await default_llm.close()
    metadata = metadata_service._metadata_service
    if metadata and metadata.llm_service:
        await metadata.llm_service.close()
    source = input_source_service._input_source_service
    if source:
        await source.close()
