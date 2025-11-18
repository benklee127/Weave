"""
Message bus for inter-agent communication.

This module provides a simple event-driven message bus that allows agents
to communicate asynchronously without tight coupling.
"""

import asyncio
from typing import Dict, List, Callable, Any
from collections import defaultdict
import logging

from src.core.schemas import AgentMessage

logger = logging.getLogger(__name__)


class MessageBus:
    """
    Event-driven message bus for agent communication.

    Agents subscribe to event types and publish messages to the bus.
    Messages are delivered asynchronously to all subscribers.
    """

    def __init__(self):
        """Initialize the message bus."""
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._message_history: List[AgentMessage] = []
        self._max_history = 1000

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: Type of event to subscribe to
            handler: Async function to call when event occurs
        """
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed handler to event: {event_type}")

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """
        Unsubscribe from an event type.

        Args:
            event_type: Type of event to unsubscribe from
            handler: Handler function to remove
        """
        if event_type in self._subscribers:
            self._subscribers[event_type].remove(handler)
            logger.debug(f"Unsubscribed handler from event: {event_type}")

    async def publish(self, message: AgentMessage) -> None:
        """
        Publish a message to all subscribers.

        Args:
            message: AgentMessage to publish
        """
        # Store in history
        self._message_history.append(message)
        if len(self._message_history) > self._max_history:
            self._message_history.pop(0)

        # Get subscribers for this event type
        handlers = self._subscribers.get(message.event_type, [])

        if not handlers:
            logger.debug(f"No subscribers for event: {message.event_type}")
            return

        logger.info(
            f"Publishing event '{message.event_type}' from {message.sender} "
            f"to {len(handlers)} subscriber(s)"
        )

        # Call all handlers asynchronously
        tasks = []
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    tasks.append(handler(message))
                else:
                    # Wrap sync handlers in async
                    tasks.append(asyncio.to_thread(handler, message))
            except Exception as e:
                logger.error(f"Error creating task for handler: {e}")

        # Wait for all handlers to complete
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Log any errors
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Handler {i} failed for event {message.event_type}: {result}"
                    )

    def get_history(
        self, event_type: Optional[str] = None, correlation_id: Optional[str] = None
    ) -> List[AgentMessage]:
        """
        Get message history, optionally filtered.

        Args:
            event_type: Filter by event type
            correlation_id: Filter by correlation ID

        Returns:
            List of AgentMessage objects
        """
        messages = self._message_history

        if event_type:
            messages = [m for m in messages if m.event_type == event_type]

        if correlation_id:
            messages = [m for m in messages if m.correlation_id == correlation_id]

        return messages

    def clear_history(self) -> None:
        """Clear the message history."""
        self._message_history.clear()
        logger.debug("Message history cleared")

    def get_subscriber_count(self, event_type: str) -> int:
        """Get number of subscribers for an event type."""
        return len(self._subscribers.get(event_type, []))
