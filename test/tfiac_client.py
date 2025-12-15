"""Test client capabilities."""

import asyncio
import socket

from tellsticknet.util import sock_recvfrom, sock_sendto

import pytfiac

STATUSUPDATEMSG = """<msg msgid="statusUpdateMsg" type="Control" seq="555">
  <statusUpdateMsg>
    <BaseMode>selfFeel</BaseMode>
    <TurnOn>on</TurnOn>
    <Opt_ECO>off</Opt_ECO>
    <Opt_super>off</Opt_super>
    <SetTemp>77</SetTemp>
    <WindSpeed>Low</WindSpeed>
    <Degree_Half>off</Degree_Half>
    <Opt_healthy>off</Opt_healthy>
    <Opt_antiMildew>off</Opt_antiMildew>
    <Opt_StereoWind>off</Opt_StereoWind>
    <Opt_heating>off</Opt_heating>
    <Countdown_Timer_Off>00:00</Countdown_Timer_Off>
    <Countdown_Timer_On>00:00</Countdown_Timer_On>
    <Infrared_Direct>off</Infrared_Direct>
    <Infrared_TurnOn>off</Infrared_TurnOn>
    <HumidityEnable>off</HumidityEnable>
    <CleannessEnable>off</CleannessEnable>
    <WindDirection_H>off</WindDirection_H>
    <WindDirection_V>off</WindDirection_V>
    <IndoorTemp>70</IndoorTemp>
    <Opt_sleepMode>off:0:0:0:0:0:0:0:0:0:0</Opt_sleepMode>
    <OutdoorTemp>0</OutdoorTemp>
    <BeepEnable>on</BeepEnable>
    <DeviceName>Katie AC</DeviceName>
  </statusUpdateMsg>
</msg>
"""

loop = asyncio.get_event_loop()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.setblocking(False)

host = "localhost"
port = pytfiac.UDP_PORT

sock.bind((host, port))


async def mock_tfiac(sock):
    """Mock tfiac unit."""
    data, addr = await sock_recvfrom(sock, 2048)
    if 'msgid="SyncStatusReq"' in data.decode():
        data = STATUSUPDATEMSG.encode()
    await sock_sendto(sock, data, addr)


async def tfiac(sock):
    """Test pytfiac."""
    tfiac_client = pytfiac.Tfiac(host)
    await tfiac_client.update()
    assert tfiac_client.status["operation"] == "selfFeel"
    assert tfiac_client.name == "Katie AC"


if __name__ == "__main__":
    try:
        loop.create_task(mock_tfiac(sock))
        loop.run_until_complete(tfiac(sock))
    finally:
        loop.close()
