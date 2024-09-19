import asyncio
import inspect
from typing import Any, Callable, Dict, Generic, List, Set, Type, TypeVar, Union

from pydantic import BaseModel

# TODO: Allow people to pass results from one method to another and not just state
# TODO: Add in thiago and eduardo suggestions
# TODO: Add the ability to for start to handle _and and _or conditions

T = TypeVar("T", bound=Union[BaseModel, Dict[str, Any]])


def start(condition=None):
    def decorator(func):
        print(f"[start decorator] Decorating start method: {func.__name__}")
        func.__is_start_method__ = True
        if condition is not None:
            if isinstance(condition, str):
                func.__trigger_methods__ = [condition]
                func.__condition_type__ = "OR"
            elif (
                isinstance(condition, dict)
                and "type" in condition
                and "methods" in condition
            ):
                func.__trigger_methods__ = condition["methods"]
                func.__condition_type__ = condition["type"]
            elif callable(condition) and hasattr(condition, "__name__"):
                func.__trigger_methods__ = [condition.__name__]
                func.__condition_type__ = "OR"
            else:
                raise ValueError(
                    "Condition must be a method, string, or a result of or_() or and_()"
                )
        return func

    return decorator


def listen(condition):
    def decorator(func):
        if isinstance(condition, str):
            func.__trigger_methods__ = [condition]
            func.__condition_type__ = "OR"
        elif (
            isinstance(condition, dict)
            and "type" in condition
            and "methods" in condition
        ):
            func.__trigger_methods__ = condition["methods"]
            func.__condition_type__ = condition["type"]
        elif callable(condition) and hasattr(condition, "__name__"):
            func.__trigger_methods__ = [condition.__name__]
            func.__condition_type__ = "OR"
        else:
            raise ValueError(
                "Condition must be a method, string, or a result of or_() or and_()"
            )
        return func

    return decorator


def router(method):
    def decorator(func):
        print(
            f"[router decorator] Decorating router: {func.__name__} for method: {method.__name__}"
        )
        func.__is_router__ = True
        func.__router_for__ = method.__name__
        return func

    return decorator


def or_(*conditions):
    methods = []
    for condition in conditions:
        if isinstance(condition, dict) and "methods" in condition:
            methods.extend(condition["methods"])
        elif isinstance(condition, str):
            methods.append(condition)
        elif callable(condition):
            methods.append(getattr(condition, "__name__", repr(condition)))
        else:
            raise ValueError("Invalid condition in or_()")
    return {"type": "OR", "methods": methods}


def and_(*conditions):
    methods = []
    for condition in conditions:
        if isinstance(condition, dict) and "methods" in condition:
            methods.extend(condition["methods"])
        elif isinstance(condition, str):
            methods.append(condition)
        elif callable(condition):
            methods.append(getattr(condition, "__name__", repr(condition)))
        else:
            raise ValueError("Invalid condition in and_()")
    return {"type": "AND", "methods": methods}


class FlowMeta(type):
    def __new__(mcs, name, bases, dct):
        cls = super().__new__(mcs, name, bases, dct)

        start_methods = []
        listeners = {}
        routers = {}

        for attr_name, attr_value in dct.items():
            if hasattr(attr_value, "__is_start_method__"):
                start_methods.append(attr_name)
                if hasattr(attr_value, "__trigger_methods__"):
                    methods = attr_value.__trigger_methods__
                    condition_type = getattr(attr_value, "__condition_type__", "OR")
                    listeners[attr_name] = (condition_type, methods)
            elif hasattr(attr_value, "__trigger_methods__"):
                methods = attr_value.__trigger_methods__
                condition_type = getattr(attr_value, "__condition_type__", "OR")
                listeners[attr_name] = (condition_type, methods)
            elif hasattr(attr_value, "__is_router__"):
                routers[attr_value.__router_for__] = attr_name

        return cls


class Flow(Generic[T], metaclass=FlowMeta):
    _start_methods: List[str] = []
    _listeners: Dict[str, tuple[str, List[str]]] = {}
    _routers: Dict[str, str] = {}
    initial_state: Union[Type[T], T, None] = None

    def __init__(self):
        print("[Flow.__init__] Initializing Flow")
        self._methods: Dict[str, Callable] = {}
        self._state = self._create_initial_state()
        self._completed_methods: Set[str] = set()
        self._pending_and_listeners: Dict[str, Set[str]] = {}

        for method_name in dir(self):
            if callable(getattr(self, method_name)) and not method_name.startswith(
                "__"
            ):
                self._methods[method_name] = getattr(self, method_name)

    def _create_initial_state(self) -> T:
        print("[Flow._create_initial_state] Creating initial state")
        if self.initial_state is None:
            return {}  # type: ignore
        elif isinstance(self.initial_state, type):
            return self.initial_state()
        else:
            return self.initial_state

    @property
    def state(self) -> T:
        return self._state

    async def kickoff(self):
        print("[Flow.kickoff] Starting kickoff")
        if not self._start_methods:
            raise ValueError("No start method defined")

        for start_method in self._start_methods:
            print(f"[Flow.kickoff] Executing start method: {start_method}")
            result = await self._execute_method(self._methods[start_method])
            await self._execute_listeners(start_method, result)

    async def _execute_method(self, method: Callable, *args, **kwargs):
        print(f"[Flow._execute_method] Executing method: {method.__name__}")
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        else:
            return method(*args, **kwargs)

    async def _execute_listeners(self, trigger_method: str, result: Any):
        print(
            f"[Flow._execute_listeners] Executing listeners for trigger method: {trigger_method}"
        )
        listener_tasks = []

        if trigger_method in self._routers:
            router_method = self._methods[self._routers[trigger_method]]
            path = await self._execute_method(router_method)
            # Use the path as the new trigger method
            trigger_method = path

        for listener, (condition_type, methods) in self._listeners.items():
            if condition_type == "OR":
                if trigger_method in methods:
                    listener_tasks.append(
                        self._execute_single_listener(listener, result)
                    )
            elif condition_type == "AND":
                if listener not in self._pending_and_listeners:
                    self._pending_and_listeners[listener] = set()
                self._pending_and_listeners[listener].add(trigger_method)
                if set(methods) == self._pending_and_listeners[listener]:
                    listener_tasks.append(
                        self._execute_single_listener(listener, result)
                    )
                    del self._pending_and_listeners[listener]

        # Run all listener tasks concurrently and wait for them to complete
        await asyncio.gather(*listener_tasks)

    async def _execute_single_listener(self, listener: str, result: Any):
        print(f"[Flow._execute_single_listener] Executing listener: {listener}")
        try:
            method = self._methods[listener]
            sig = inspect.signature(method)
            if len(sig.parameters) > 1:  # More than just 'self'
                listener_result = await self._execute_method(method, result)
            else:
                listener_result = await self._execute_method(method)
            await self._execute_listeners(listener, listener_result)
        except Exception as e:
            print(f"[Flow._execute_single_listener] Error in method {listener}: {e}")
            import traceback

            traceback.print_exc()
