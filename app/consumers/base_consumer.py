# File: app/consumers/base_consumer.py
"""
Base RabbitMQ Consumer.

Shared connection logic, retry handling, and graceful shutdown.
All consumers inherit from this class.

Usage:
    class MyConsumer(BaseConsumer):
        def process_message(self, data):
            print(data)

    consumer = MyConsumer(queue_name="my_queue")
    consumer.start()
"""

import os
import json
import logging
import pika
import time
import signal
import sys

from app.core.config import settings

logger = logging.getLogger(__name__)

EXCHANGE_NAME = "disaster_events"


class BaseConsumer:
    """Base class for all RabbitMQ consumers."""

    def __init__(self, queue_name: str):
        self.queue_name = queue_name
        # self.rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
        self.rabbitmq_url = settings.RABBITMQ_URL
        self.connection = None
        self.channel = None
        self._running = True

        # Graceful shutdown on Ctrl+C
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, signum, frame):
        """Handle graceful shutdown."""
        logger.info(f"[{self.queue_name}] Shutting down consumer...")
        self._running = False
        if self.connection and not self.connection.is_closed:
            self.connection.close()
        sys.exit(0)

    def connect(self):
        """Connect to RabbitMQ with retry logic."""
        max_retries = 5
        retry_delay = 3

        for attempt in range(1, max_retries + 1):
            try:
                params = pika.URLParameters(self.rabbitmq_url)
                self.connection = pika.BlockingConnection(params)
                self.channel = self.connection.channel()

                # Ensure queue exists
                self.channel.queue_declare(queue=self.queue_name, durable=True)

                # Fair dispatch — only send 1 message at a time to this consumer
                self.channel.basic_qos(prefetch_count=1)

                logger.info(f"[{self.queue_name}] Connected to RabbitMQ (attempt {attempt})")
                return True

            except Exception as e:
                logger.error(f"[{self.queue_name}] Connection failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    time.sleep(retry_delay)

        logger.error(f"[{self.queue_name}] Failed to connect after {max_retries} attempts")
        return False

    def start(self):
        """Start consuming messages from the queue."""
        if not self.connect():
            return

        logger.info(f"[{self.queue_name}] Waiting for messages... (Ctrl+C to exit)")
        print(f"\n{'='*60}")
        print(f"  CONSUMER: {self.queue_name}")
        print(f"  Status: Listening for messages...")
        print(f"  Press Ctrl+C to stop")
        print(f"{'='*60}\n")

        def callback(ch, method, properties, body):
            try:
                data = json.loads(body)
                event_type = data.get("event_type", "unknown")

                print(f"\n[{self.queue_name}] Received: {event_type}")
                print(f"  Disaster ID: {data.get('disaster_id', 'N/A')}")
                print(f"  Tracking ID: {data.get('tracking_id', 'N/A')}")

                # Call the child class's process method
                self.process_message(data)

                # Acknowledge message — removes from queue
                ch.basic_ack(delivery_tag=method.delivery_tag)
                print(f"  Status: ✅ Processed and acknowledged")

            except Exception as e:
                logger.error(f"[{self.queue_name}] Error processing message: {e}")
                print(f"  Status: ❌ Error: {e}")
                # Reject and requeue on failure
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        self.channel.basic_consume(
            queue=self.queue_name,
            on_message_callback=callback,
            auto_ack=False,
        )

        try:
            self.channel.start_consuming()
        except KeyboardInterrupt:
            self._shutdown(None, None)

    def process_message(self, data: dict):
        """
        Override in child class.
        This is where you put your business logic.
        """
        raise NotImplementedError("Subclasses must implement process_message()")