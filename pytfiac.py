"""Python3 library for climate device using the TFIAC protocol."""
import asyncio
import logging
import socket

import xmltodict

__version__ = "0.4"

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "Tfiac",
    "Unavailable",
    "ON_MODE",
    "OPERATION_MODE",
    "TARGET_TEMP",
    "FAN_MODE",
    "SWING_MODE",
    "SET_SWING",
    "OPERATION_LIST",
    "FAN_LIST",
    "SWING_LIST",
    "MIN_TEMP",
    "MAX_TEMP",
]

UDP_PORT = 7777
MIN_TEMP = 61
MAX_TEMP = 88

SHORT_WAIT = 2

OPERATION_LIST = ['heat', 'selfFeel', 'dehumi', 'fan', 'cool']
FAN_LIST = ['Auto', 'Low', 'Middle', 'High']
SWING_LIST = [
    'Off',
    'Vertical',
    'Horizontal',
    'Both',
]
CURR_TEMP = 'current_temp'
TARGET_TEMP = 'target_temp'
OPERATION_MODE = 'operation'
FAN_MODE = 'fan_mode'
SWING_MODE = 'swing_mode'
ON_MODE = 'is_on'

STATUS_MESSAGE = '<msg msgid="SyncStatusReq" type="Control" seq="{seq}">' \
                 '<SyncStatusReq></SyncStatusReq></msg>'
SET_MESSAGE = '<msg msgid="SetMessage" type="Control" seq="{seq}">' + \
              '<SetMessage>{message}</SetMessage></msg>'

UPDATE_MESSAGE = '<TurnOn>{{{}}}</TurnOn>'.format(ON_MODE) + \
                 '<BaseMode>{{{}}}</BaseMode>'.format(OPERATION_MODE) + \
                 '<SetTemp>{{{}}}</SetTemp>'.format(TARGET_TEMP) + \
                 '<WindSpeed>{{{}}}</WindSpeed>'.format(FAN_MODE)

SET_SWING_OFF = '<WindDirection_H>off</WindDirection_H>' \
                '<WindDirection_V>off</WindDirection_V>'
SET_SWING_3D = '<WindDirection_H>on</WindDirection_H>' \
               '<WindDirection_V>on</WindDirection_V>'
SET_SWING_VERTICAL = '<WindDirection_H>off</WindDirection_H>' \
                     '<WindDirection_V>on</WindDirection_V>'
SET_SWING_HORIZONTAL = '<WindDirection_H>on</WindDirection_H>' \
                       '<WindDirection_V>off</WindDirection_V>'

SET_SWING = {
    'Off': SET_SWING_OFF,
    'Vertical': SET_SWING_VERTICAL,
    'Horizontal': SET_SWING_HORIZONTAL,
    'Both': SET_SWING_3D,
}


class Unavailable(Exception):
    """Raised when the socket timeout."""


class Tfiac():
    """TFIAC class to handle connections."""

    def __init__(self, host):
        """Init class."""
        self._host = host
        self._status = {}
        self._name = None
        self._available = True
        self._last_seq = 0

    @property
    def available(self):
        """Return if the device is available."""
        return self._available

    @property
    def _seq(self):
        from time import time
        return str(int(time() * 1000))[-7:]

    async def _send(self, message):
        """Send message."""
        _LOGGER.debug("Sending message: %s", message.encode())

        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: asyncio.DatagramProtocol(),
            remote_addr=(self._host, UDP_PORT)
        )
        transport.sendto(message.encode())
        try:
            data, _ = await asyncio.wait_for(
                loop.sock_recv(transport.get_extra_info('socket'), 1024), timeout=5
            )
            return data
        except asyncio.TimeoutError:
            self._available = False
            raise Unavailable()
        finally:
            transport.close()
        return

    async def update(self):
        """Update the state of the A/C."""
        from time import time
        if time() - self._last_seq < SHORT_WAIT:
            return
        response = await self._send(STATUS_MESSAGE.format(seq=self._seq))
        try:
            _status = dict(xmltodict.parse(response)['msg']['statusUpdateMsg'])
            _LOGGER.debug("Current status %s", _status)
            self._name = _status['DeviceName']
            self._status[CURR_TEMP] = round(float(_status['IndoorTemp']), 2)
            self._status[TARGET_TEMP] = round(float(_status['SetTemp']), 2)
            self._status[OPERATION_MODE] = _status['BaseMode']
            self._status[FAN_MODE] = _status['WindSpeed']
            self._status[ON_MODE] = _status['TurnOn']
            self._status[SWING_MODE] = self._map_winddirection(_status)
        except Exception as ex:  # pylint: disable=W0703
            _LOGGER.error(ex)
        else:
            self._last_seq = time()

    def _map_winddirection(self, _status):
        """Map WindDirection to swing_mode."""
        value = 0
        if _status['WindDirection_H'] == 'on':
            value = 1
        if _status['WindDirection_V'] == 'on':
            value |= 2
        return {0: 'Off', 1: 'Horizontal', 2: 'Vertical', 3: 'Both'}[value]

    async def set_state(self, mode, value):
        """Set the new state of the ac."""
        await self.update()  # make sure we have the latest settings.
        self._status.update({mode: value})
        if mode == OPERATION_MODE:
            self._status.update({ON_MODE: "on"})
        await self._send(
            SET_MESSAGE.format(seq=self._seq,
                               message=UPDATE_MESSAGE).format(**self._status))

    async def set_swing(self, value):
        """Set swing mode."""
        await self._send(
            SET_MESSAGE.format(seq=self._seq, message=SET_SWING[value])
        )

    @property
    def name(self):
        """Return name of device."""
        return self._name

    @property
    def status(self):
        """Return dict of current status."""
        return self._status
