"""SIP test bot: echoes the caller's audio, forwards the call on any key press."""

import asyncio
import enum
import json
import logging
import os
import sys

from livekit import agents, api, rtc

logger = logging.getLogger("echo-agent")

SAMPLE_RATE = 48000
SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP


class TransferMode(enum.StrEnum):
    # Dial the target into this room through the bot's outbound trunk and let
    # LiveKit mix caller and target. LiveKit stays in the media path.
    BRIDGE = "bridge"
    # Hand the caller's leg to the target with a SIP REFER. The caller's
    # provider places the new call and LiveKit drops out.
    REFER = "refer"


def transfer_mode() -> TransferMode:
    return TransferMode(os.environ.get("TRANSFER_MODE", TransferMode.BRIDGE))


async def provision_sip() -> None:
    """Replace this bot's inbound trunk, outbound trunk and dispatch rule.

    Everything is named after AGENT_NAME, so a restart deletes exactly what the
    previous start created and nothing that belongs to another deployment.
    """
    name = os.environ["AGENT_NAME"]
    number = os.environ["SIP_NUMBER"]
    async with api.LiveKitAPI() as lk:
        rules = await lk.sip.list_dispatch_rule(api.ListSIPDispatchRuleRequest())
        for rule in rules.items:
            if rule.name == f"{name}-dispatch":
                await lk.sip.delete_dispatch_rule(
                    api.DeleteSIPDispatchRuleRequest(
                        sip_dispatch_rule_id=rule.sip_dispatch_rule_id
                    )
                )
        inbound = await lk.sip.list_inbound_trunk(api.ListSIPInboundTrunkRequest())
        outbound = await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
        for trunk in [*inbound.items, *outbound.items]:
            if trunk.name in (f"{name}-inbound", f"{name}-outbound"):
                await lk.sip.delete_trunk(
                    api.DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id)
                )

        inbound_trunk = await lk.sip.create_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(name=f"{name}-inbound", numbers=[number])
            )
        )
        outbound_trunk = await lk.sip.create_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(
                trunk=api.SIPOutboundTrunkInfo(
                    name=f"{name}-outbound",
                    numbers=[number],
                    address=os.environ["SIP_OUTBOUND_ADDRESS"],
                    auth_username=os.environ["SIP_OUTBOUND_USERNAME"],
                    auth_password=os.environ["SIP_OUTBOUND_PASSWORD"],
                )
            )
        )
        # The job processes learn the outbound trunk from the dispatch metadata.
        metadata = json.dumps({"outbound_trunk_id": outbound_trunk.sip_trunk_id})
        rule = await lk.sip.create_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                dispatch_rule=api.SIPDispatchRuleInfo(
                    name=f"{name}-dispatch",
                    trunk_ids=[inbound_trunk.sip_trunk_id],
                    rule=api.SIPDispatchRule(
                        dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                            room_prefix=f"{name}-"
                        )
                    ),
                    room_config=api.RoomConfiguration(
                        agents=[
                            api.RoomAgentDispatch(agent_name=name, metadata=metadata)
                        ]
                    ),
                )
            )
        )
    # print, not logger: the agents CLI sets up logging only after this ran.
    print(
        f"SIP for {number}: inbound {inbound_trunk.sip_trunk_id}, "
        f"outbound {outbound_trunk.sip_trunk_id}, "
        f"dispatch rule {rule.sip_dispatch_rule_id}"
    )


async def echo(caller: rtc.RemoteParticipant, source: rtc.AudioSource) -> None:
    stream = rtc.AudioStream.from_participant(
        participant=caller,
        track_source=rtc.TrackSource.SOURCE_MICROPHONE,
        sample_rate=SAMPLE_RATE,
    )
    async for event in stream:
        await source.capture_frame(event.frame)


async def entrypoint(ctx: agents.JobContext) -> None:
    await ctx.connect(auto_subscribe=agents.AutoSubscribe.AUDIO_ONLY)
    caller = await ctx.wait_for_participant(kind=SIP)
    logger.info("call from %s in room %s", caller.identity, ctx.room.name)
    outbound_trunk_id = json.loads(ctx.job.metadata)["outbound_trunk_id"]

    # Either side hanging up ends the call for everyone, also after the bridge.
    @ctx.room.on("participant_disconnected")
    def _on_leave(participant: rtc.RemoteParticipant) -> None:
        if participant.kind == SIP:
            logger.info("%s hung up, closing room", participant.identity)
            ctx.delete_room()

    source = rtc.AudioSource(SAMPLE_RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track("echo", source)
    await ctx.room.local_participant.publish_track(
        track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    )
    echo_task = asyncio.create_task(echo(caller, source))
    transfer_task: asyncio.Task[None] | None = None

    async def bridge(target: str) -> None:
        await ctx.api.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                room_name=ctx.room.name,
                sip_trunk_id=outbound_trunk_id,
                sip_call_to=target,
                participant_identity="transfer-target",
                wait_until_answered=True,
                play_dialtone=True,
            )
        )
        logger.info("%s answered, call bridged", target)

    async def refer(target: str) -> None:
        # Returns once the caller's side accepted the REFER; the caller then
        # leaves the room, which closes it.
        await ctx.api.sip.transfer_sip_participant(
            api.TransferSIPParticipantRequest(
                room_name=ctx.room.name,
                participant_identity=caller.identity,
                transfer_to=f"tel:{target}",
                play_dialtone=True,
            )
        )
        logger.info("caller referred to %s", target)

    async def transfer() -> None:
        nonlocal echo_task, transfer_task
        # The echo track stays published but silent: a bridge mixes caller and
        # target directly, a REFER takes the caller out of the room.
        echo_task.cancel()
        target = os.environ["TRANSFER_TO"]
        mode = transfer_mode()
        logger.info("forwarding call to %s by %s", target, mode)
        try:
            await (bridge if mode is TransferMode.BRIDGE else refer)(target)
        except api.TwirpError as e:
            logger.error("forwarding to %s failed: %s %s", target, e.code, e.message)
            echo_task = asyncio.create_task(echo(caller, source))
            transfer_task = None

    @ctx.room.on("sip_dtmf_received")
    def _on_dtmf(dtmf: rtc.SipDTMF) -> None:
        nonlocal transfer_task
        logger.info("DTMF %r from %s", dtmf.digit, dtmf.participant)
        if transfer_task is None:
            transfer_task = asyncio.create_task(transfer())


if __name__ == "__main__":
    transfer_mode()  # fail at startup on a typo, not on the first key press
    if sys.argv[1:2] in (["start"], ["dev"]):
        asyncio.run(provision_sip())
    server = agents.AgentServer()
    # An explicit name: the agent joins only rooms a dispatch rule sends to it,
    # never every room on the LiveKit server.
    server.rtc_session(entrypoint, agent_name=os.environ["AGENT_NAME"])
    agents.cli.run_app(server)
