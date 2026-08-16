# Helpers for Amaranth tests that heavily use streams.
#
# These were lifted from:
# URL: https://github.com/zyp/katsuo-stream
# License: MIT
# Author: Vegard Storheil Eriksen <zyp@jvnv.net>
#

from amaranth.lib import stream
from amaranth.sim import DomainReset, SimulatorContext

async def wait_until(ctx: SimulatorContext, condition):
    """Wait for a condition using cancellable one-shot simulator ticks."""
    while True:
        _, reset, done = await ctx.tick().sample(condition)
        if reset:
            raise DomainReset
        if done:
            return

async def put(ctx: SimulatorContext, stream: stream.Interface, payload):
    ctx.set(stream.valid, 1)
    ctx.set(stream.payload, payload)
    while True:
        _, reset, ready = await ctx.tick().sample(stream.ready)
        if reset:
            raise DomainReset
        if ready:
            break
    ctx.set(stream.valid, 0)

async def get(ctx: SimulatorContext, stream: stream.Interface):
    ctx.set(stream.ready, 1)
    while True:
        _, reset, valid, payload = await ctx.tick().sample(
            stream.valid, stream.payload
        )
        if reset:
            raise DomainReset
        if valid:
            break
    ctx.set(stream.ready, 0)
    return payload
