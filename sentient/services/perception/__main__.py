"""Entry point for: python3 -m sentient.services.perception"""
import asyncio
from sentient.services.perception.api import main

asyncio.run(main())
