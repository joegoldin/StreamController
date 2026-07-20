"""
Author: Core447
Year: 2023

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
any later version.

This programm comes with ABSOLUTELY NO WARRANTY!

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
"""
# Import Python modules
import threading
import time
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.Devices import StreamDeck
from StreamDeck.ImageHelpers import PILHelper
from loguru import logger as log
from usbmonitor import USBMonitor
import usb.core
import usb.util
import os
import types


# Import own modules
from src.backend.DeckManagement.Subclasses.RemoteDeckManager import RemoteDeckManager
from src.backend.DeckManagement.Subclasses.RemoteDeck import RemoteDeck
from src.backend.DeckManagement.BetterDeck import BetterDeck
from src.backend.DeckManagement.DeckController import DeckController
from src.backend.PageManagement.PageManagerBackend import PageManagerBackend
from src.backend.SettingsManager import SettingsManager
from src.backend.DeckManagement.HelperMethods import get_sys_param_value, recursive_hasattr
from src.backend.DeckManagement.Subclasses.FakeDeck import FakeDeck
from src.backend.DeckManagement.StreamDockDeck import (
    enumerate_stream_dock_decks,
    STREAMDOCK_VENDOR_ID_STRINGS,
)

from src.backend.DeckManagement.beta_resume import _read as beta_read

# Import globals first to get IS_MAC
import globals as gl

import gi
from gi.repository import GLib

if not gl.IS_MAC:
    gi.require_version("Xdp", "1.0")
    from gi.repository import Xdp

ELGATO_VENDOR_ID = "0fd9"


class DeckManager:
    def __init__(self):
        #TODO: Maybe outsource some objects
        self.deck_controller: list[DeckController] = []
        self.fake_deck_controller = []
        self.settings_manager = SettingsManager()
        self.page_manager = gl.page_manager
        # self.page_manager.load_pages()

        # USB monitor to detect connections and disconnections
        self.usb_monitor = USBMonitor()
        self.usb_monitor.start_monitoring(on_connect=self.on_connect, on_disconnect=self.on_disconnect)

        self.flatpak_disconnect_thread = FlatpakDeckDisconnectThread(self)

        self.flatpak = False
        if not gl.IS_MAC:
            portal = Xdp.Portal.new()
            self.flatpak = portal.running_under_flatpak() # on_disconnect is not working under Flatpak - we use a separate thread #TODO: Find a better solution
        if self.flatpak:
            log.info("Running under Flatpak. Using separate thread to detect device disconnection.")
            self.flatpak_disconnect_thread.start()

        self.beta_resume_mode = gl.settings_manager.get_app_settings().get("system", {}).get("beta-resume-mode", True)
        log.info(f"Beta resume mode: {self.beta_resume_mode}")

        resume_thread = DetectResumeThread(self)
        if not self.beta_resume_mode:
            resume_thread.start()

        # A deck that re-enumerates on resume from suspend is missed by usbmonitor
        # -- it doesn't receive the udev connect/disconnect uevents fired during
        # the suspend/resume transition -- so on_connect never runs and the deck
        # would sit dead (this bites StreamDock panels with power/persist=0, which
        # re-enumerate on resume). Reconnect them explicitly on a boottime-detected
        # resume. Runs regardless of beta_resume_mode; it only touches decks that
        # report disconnected, so live decks are untouched.
        self.stream_dock_resume_thread = StreamDockResumeThread(self)
        self.stream_dock_resume_thread.start()

        self.remote_deck_manager = RemoteDeckManager(self)
        if gl.settings_manager.get_app_settings().get("dev", {}).get("n-remote-decks", 0) > 0:
            self.load_remote_decks()


    def load_remote_decks(self):
        print(" load remote decks")
        self.remote_deck_manager.start()
        for controller in self.remote_deck_manager.deck_controllers:
            if controller in self.deck_controller:
                continue

            self.deck_controller.append(controller)
            if recursive_hasattr(gl, "app.main_win.leftArea.deck_stack"):
                # Add to deck stack
                for controller in self.remote_deck_manager.deck_controllers:
                    GLib.idle_add(gl.app.main_win.leftArea.deck_stack.add_page, controller)

        if recursive_hasattr(gl, "app.main_win.sidebar.page_selector"):
            GLib.idle_add(gl.app.main_win.sidebar.page_selector.update)

        if recursive_hasattr(gl, "app.main_win"):
            GLib.idle_add(gl.app.main_win.check_for_errors)

    def remove_remote_decks(self):
        for controller in list(self.remote_deck_manager.deck_controllers):
            self.remove_controller(controller)
        if recursive_hasattr(gl, "app.main_win"):
            GLib.idle_add(gl.app.main_win.check_for_errors)
        self.remote_deck_manager.stop()

    def load_decks(self):
        if not gl.argparser.parse_args().skip_load_hardware_decks:
            self.load_hardware_decks()

        self.load_fake_decks()
    
    def load_hardware_decks(self):
        if gl.IS_MAC:
            return
        decks = list(DeviceManager().enumerate())
        # MiraBox StreamDock devices (Stream Deck clones) speak their own HID
        # protocol; they are wrapped to look like a StreamDeck device.
        decks.extend(enumerate_stream_dock_decks())
        for deck in decks:
            try:
                if not deck.is_open():
                    deck.open(self.beta_resume_mode)
            except:
                log.error("Failed to open deck. Maybe it's already connected to another instance?")
                continue
            try:
                deck_controller = DeckController(self, deck)
                self.deck_controller.append(deck_controller)
            except Exception as e:
                log.error(f"Failed to initialize deck controller: {e}. Skipping this deck.")
                try:
                    deck.close()
                except:
                    pass

    def load_fake_decks(self):
        old_n_fake_decks = len(self.fake_deck_controller)
        n_fake_decks = int(gl.settings_manager.load_settings_from_file(os.path.join(gl.DATA_PATH, "settings", "settings.json")).get("dev", {}).get("n-fake-decks", 0))

        if n_fake_decks > old_n_fake_decks:
            log.info(f"Loading {n_fake_decks - old_n_fake_decks} fake deck(s)")
            # Load difference in number of fake decks
            for controller in range(n_fake_decks - old_n_fake_decks):
                a = f"Fake Deck {len(self.fake_deck_controller)+1}"
                fake_deck = FakeDeck(serial_number = f"fake-deck-{len(self.fake_deck_controller)+1}", deck_type=f"Fake Deck {len(self.fake_deck_controller)+1}")
                self.add_newly_connected_deck(fake_deck, is_fake=True)

            # Update header deck switcher if the new deck is the only one
            if len(self.deck_controller) == 1 and False:
                # Check if ui is loaded - if not it will grab the controller automatically
                if recursive_hasattr(gl, "app.main_win.header_bar.deckSwitcher"):
                    gl.app.main_win.header_bar.deckSwitcher.set_show_switcher(True)

        elif n_fake_decks < old_n_fake_decks:
            # Remove difference in number of fake decks
            log.info(f"Removing {old_n_fake_decks - n_fake_decks} fake deck(s)")
            for controller in self.fake_deck_controller[-(old_n_fake_decks - n_fake_decks):]:
                # Remove controller from fake_decks
                self.fake_deck_controller.remove(controller)
                # Remove controller from main list
                self.deck_controller.remove(controller)
                # Remove deck page on stack
                gl.app.main_win.leftArea.deck_stack.remove_page(controller)

            # Update header deck switcher if there are no more decks
            if len(self.deck_controller) == 0 and False:
                # Check if ui is loaded - if not it will grab the controller automatically
                if recursive_hasattr(gl, "app.main_win.header_bar.deckSwitcher"):
                    gl.app.main_win.header_bar.deckSwitcher.set_show_switcher(False)
        if hasattr(gl.app, "main_win"):
            GLib.idle_add(gl.app.main_win.check_for_errors)

    def on_connect(self, device_id, device_info):
        log.info(f"Device {device_id} with info: {device_info} connected")
        # Check if it is a supported device (Elgato Stream Deck or MiraBox StreamDock)
        vendor_id = device_info["ID_VENDOR_ID"]
        if vendor_id != ELGATO_VENDOR_ID and vendor_id not in STREAMDOCK_VENDOR_ID_STRINGS:
            return

        GLib.idle_add(self.connect_new_decks)

    def connect_new_decks(self):
        # A fast USB reset/replug can reuse the same hidraw path while
        # usbmonitor delivers only the new connect event. Drop the dead
        # controller before comparing ids, otherwise the replacement deck is
        # mistaken for the already-loaded one and never added back.
        self._remove_disconnected_decks()

        # Get already loaded deck serial ids
        loaded_deck_ids = []
        for controller in self.deck_controller:
            loaded_deck_ids.append(controller.deck.id())

        for deck in list(DeviceManager().enumerate()) + enumerate_stream_dock_decks():
            if deck.id() in loaded_deck_ids:
                continue
            # Add deck
            self.add_newly_connected_deck(deck)

        if recursive_hasattr(gl, "app.main_win"):
            GLib.idle_add(gl.app.main_win.check_for_errors)

    def _remove_disconnected_decks(self) -> None:
        for controller in list(self.deck_controller):
            try:
                if controller.deck.connected():
                    continue
                log.info(f"Removing disconnected deck before enumeration: {controller.deck.id()}")
                try:
                    controller.deck.close()
                except Exception as e:
                    log.warning(f"Failed to close disconnected deck: {e}")
                self.remove_controller(controller)
            except Exception as e:
                log.error(f"Failed to reconcile disconnected deck: {e}")


    def on_disconnect(self, device_id, device_info):
        log.info(f"Device {device_id} with info: {device_info} disconnected")
        vendor_id = device_info["ID_VENDOR_ID"]
        if vendor_id != ELGATO_VENDOR_ID and vendor_id not in STREAMDOCK_VENDOR_ID_STRINGS:
            return

        self._remove_disconnected_decks()

        if recursive_hasattr(gl, "app.main_win"):
            GLib.idle_add(gl.app.main_win.check_for_errors)

    def remove_controller(self, deck_controller: DeckController) -> None:
        if deck_controller in self.deck_controller:
            self.deck_controller.remove(deck_controller)
        if recursive_hasattr(gl, "app.main_win.leftArea.deck_stack"):
            GLib.idle_add(gl.app.main_win.leftArea.deck_stack.remove_page, deck_controller)
        deck_controller.delete()

    def get_controller_for_deck(self, deck: StreamDeck) -> DeckController | None:
        for controller in self.deck_controller:
            if controller.deck is deck:
                return controller

    def add_newly_connected_deck(self, deck:StreamDeck, is_fake: bool = False):
        try:
            deck_controller = DeckController(self, deck)
        except Exception as e:
            log.error(f"Failed to initialize deck controller for newly connected deck: {e}")
            try:
                deck.close()
            except:
                pass
            return

        # Check if ui is loaded - if not it will grab the controller automatically
        if recursive_hasattr(gl, "app.main_win.leftArea.deck_stack"):
            # Add to deck stack
            GLib.idle_add(gl.app.main_win.leftArea.deck_stack.add_page, deck_controller)

        if recursive_hasattr(gl, "app.main_win.sidebar.page_selector"):
            GLib.idle_add(gl.app.main_win.sidebar.page_selector.update)



        self.deck_controller.append(deck_controller)
        if is_fake:
            self.fake_deck_controller.append(deck_controller)

        if recursive_hasattr(gl, "app.main_win"):
            GLib.idle_add(gl.app.main_win.check_for_errors)

    def close_all(self):
        log.info("Closing all decks")
        for controller in self.deck_controller:
            if controller.deck is None:
                return
            if not controller.deck.is_open():
                return
            
            log.info(f"Closing deck: {controller.deck.get_serial_number()}")
            controller.clear()
            controller.deck.close()

    def stop_usb_monitoring(self):
        self.usb_monitor.stop_monitoring(timeout=2)

    def reset_all_decks(self):
        # Find all USB devices
        devices = usb.core.find(find_all=True)
        for device in devices:
            try:
                # Check if it's a StreamDeck
                if device.idVendor == DeviceManager.USB_VID_ELGATO and device.idProduct in [
                    DeviceManager.USB_PID_STREAMDECK_ORIGINAL,
                    DeviceManager.USB_PID_STREAMDECK_ORIGINAL_V2,
                    DeviceManager.USB_PID_STREAMDECK_MINI,
                    DeviceManager.USB_PID_STREAMDECK_XL,
                    DeviceManager.USB_PID_STREAMDECK_MK2,
                    DeviceManager.USB_PID_STREAMDECK_PEDAL,
                    DeviceManager.USB_PID_STREAMDECK_PLUS,
                    DeviceManager.USB_PID_STREAMDECK_NEO
                ]:
                    # Reset deck
                    usb.util.dispose_resources(device)
                    device.reset()
            except:
                log.error("Failed to reset deck, maybe it's already connected to another instance? Skipping...")

    def get_device_by_serial(self, serial: str):
        for deck in DeviceManager().enumerate():
            if not deck.is_open():
                try:
                    deck.open()
                except:
                    return
            if deck.get_serial_number() == serial:
                return deck

    def on_resumed(self):
        log.info("Resume from suspend detected, reloading decks...")
        time.sleep(2) # Give the kernel some time to handle the usb devices
        n_removed = 0
        for deck_controller in self.deck_controller:
            new_device = self.get_device_by_serial(deck_controller.serial_number())
            if new_device:
                log.info(f"Replacing deck")
                current_rotation = deck_controller.deck.get_rotation()
                deck_controller.deck = BetterDeck(new_device, current_rotation)
                deck_controller.update_all_inputs()

                deck_controller.deck.set_key_callback(deck_controller.key_event_callback)
                deck_controller.deck.set_dial_callback(deck_controller.dial_event_callback)
                deck_controller.deck.set_touchscreen_callback(deck_controller.touchscreen_event_callback)

                # deck_controller.deck._setup_reader(deck_controller.deck._read)

            else:
                n_removed += 1
                log.info(f"Removing deck")
                deck_controller.deck.close()
                deck_controller.media_player.running = False
                self.remove_controller(deck_controller)

        if n_removed > 0:
            GLib.idle_add(self.connect_new_decks)

    def get_connected_serials(self) -> list[str]:
        return [controller.serial_number() for controller in self.deck_controller]

    def reconnect_disconnected_decks(self):
        """Drop any deck that reports disconnected and re-enumerate to re-add it.

        Called on resume from suspend to run the reconnect that usbmonitor missed
        (it doesn't see the uevents fired during the suspend/resume transition).
        Live decks report ``connected() == True`` and are left alone; a deck that
        re-enumerated on resume (e.g. a StreamDock with power/persist=0) reports
        disconnected, so it's closed, removed, and re-added fresh by
        ``connect_new_decks`` -- the same path a physical replug takes, which is
        what reliably brings the panel back.
        """
        for controller in list(self.deck_controller):
            try:
                if controller.deck.connected():
                    continue
                # Prefer reviving the deck in place: for StreamDocks,
                # reinit_panel USB-resets the device, reopens it with the
                # official handshake and repaints -- clearing the firmware's
                # post-suspend "host gone" latch without any UI churn. The
                # recovery runs in a background thread (~3s); if the deck is
                # still dead afterwards (e.g. truly unplugged during suspend),
                # fall back to the remove + re-enumerate path.
                reinit = getattr(controller.deck, "reinit_panel", None)
                if callable(reinit):
                    log.info(f"Resume: hard-resetting deck {controller.serial_number()}")
                    reinit()
                    GLib.timeout_add_seconds(10, self._drop_if_still_disconnected, controller)
                    continue
                log.info(f"Resume: reconnecting disconnected deck {controller.serial_number()}")
                try:
                    controller.deck.close()
                except Exception:
                    pass
                controller.media_player.running = False
                self.remove_controller(controller)
            except Exception as e:
                log.error(f"Resume reconcile error: {e}")
        self.connect_new_decks()
        if recursive_hasattr(gl, "app.main_win"):
            GLib.idle_add(gl.app.main_win.check_for_errors)
        return False  # one-shot for GLib.idle_add

    def _drop_if_still_disconnected(self, controller) -> bool:
        """Post-reset fallback: if an in-place hard reset didn't revive the deck
        (it was actually unplugged), remove it and re-enumerate."""
        try:
            if controller in self.deck_controller and not controller.deck.connected():
                log.info(f"Deck {controller.serial_number()} still disconnected after reset; removing")
                try:
                    controller.deck.close()
                except Exception:
                    pass
                controller.media_player.running = False
                self.remove_controller(controller)
                self.connect_new_decks()
                if recursive_hasattr(gl, "app.main_win"):
                    GLib.idle_add(gl.app.main_win.check_for_errors)
        except Exception as e:
            log.error(f"Post-reset reconcile error: {e}")
        return False  # one-shot for GLib.timeout_add_seconds


class FlatpakDeckDisconnectThread(threading.Thread):
    def __init__(self, deck_manager: DeckManager):
        super().__init__(name="FlatpakDeckDisconnectThread")
        self.deck_manager = deck_manager

    def run(self):
        while gl.threads_running:
            time.sleep(2)
            for controller in list(self.deck_manager.deck_controller):
                if not controller.deck.connected():
                    self.deck_manager.remove_controller(controller)
                    if recursive_hasattr(gl, "app.main_win"):
                        GLib.idle_add(gl.app.main_win.check_for_errors)

class DetectResumeThread(threading.Thread):
    def __init__(self, deck_manager: DeckManager):
        super().__init__(name="DetectResumeThread")
        self.deck_manager = deck_manager

        self.last_1 = time.time()
        self.last_2 = time.time()

    def run(self):
        while gl.threads_running:
            self.last_1 = time.time()
            if time.time() - self.last_1 >= 5 or time.time() - self.last_2 >= 5:
                self.deck_manager.on_resumed()
            self.last_2 = time.time()
            if time.time() - self.last_1 >= 5 or time.time() - self.last_2 >= 5:
                self.deck_manager.on_resumed()

            time.sleep(2)


class StreamDockResumeThread(threading.Thread):
    """Reconnect decks after resume from suspend.

    usbmonitor does not receive the udev connect/disconnect events fired during
    the suspend/resume transition, so a deck that re-enumerates on resume (e.g. a
    StreamDock with power/persist=0) never gets on_connect and would sit dead.
    Detect resume by watching the CLOCK_BOOTTIME/CLOCK_MONOTONIC gap -- boottime
    counts time spent suspended, monotonic does not, so a jump means we slept
    (NTP-immune) -- then re-run the reconnect usbmonitor missed.
    """

    RESUME_GAP = 5.0  # seconds of unaccounted wall time => a suspend happened

    def __init__(self, deck_manager: DeckManager):
        super().__init__(name="StreamDockResumeThread", daemon=True)
        self.deck_manager = deck_manager

    @staticmethod
    def _suspend_offset() -> float:
        try:
            return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
        except (AttributeError, OSError):
            return 0.0

    def run(self):
        last = self._suspend_offset()
        while gl.threads_running:
            time.sleep(2)
            offset = self._suspend_offset()
            if offset - last > self.RESUME_GAP:
                log.info(f"Resume from suspend detected (~{offset - last:.0f}s); reconnecting decks")
                time.sleep(2)  # let the kernel finish re-enumerating USB
                GLib.idle_add(self.deck_manager.reconnect_disconnected_decks)
                offset = self._suspend_offset()  # account for the settle sleep
            last = offset
