import importlib

from madro.retrieval_agents.base import RetrievalAgent


def load_local_agent_class(uri: str) -> type[RetrievalAgent]:
    module_path = uri.removeprefix("local://").removesuffix(".py").replace("/", ".")
    module = importlib.import_module(module_path)
    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, RetrievalAgent) and attr is not RetrievalAgent:
            return attr
    raise ValueError(f"No RetrievalAgent subclass found in {uri}")


def load_local_agent(uri: str) -> RetrievalAgent:
    return load_local_agent_class(uri)()
