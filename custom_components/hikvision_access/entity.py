"""Base entity and shared helpers for Hikvision Access Control."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_MASK_CARD_DATA, DOMAIN, MANUFACTURER, OPT_MASK_CARD_DATA
from .coordinator import HikvisionAccessCoordinator


def mask_identifier(value: str) -> str:
    """Mask an identifier down to its last four characters (``****3721``).

    Card and employee numbers end up in recorder history, backups and
    screenshots via entity attributes — masked by default, full values only
    behind the options-flow switch.
    """
    if not value:
        return value
    if len(value) <= 4:
        return "****"
    return f"****{value[-4:]}"


class HikvisionAccessEntity(CoordinatorEntity[HikvisionAccessCoordinator]):
    """Base class: one device per config entry, defensive data access."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HikvisionAccessCoordinator, unique_id_suffix: str
    ) -> None:
        """Set the unique id from the entry id plus a stable suffix."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{unique_id_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        """Group every entity under the one device of this entry."""
        data = self.coordinator.data
        info = data.device_info if data else None
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id)},
            name=self.coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=info.model if info else None,
            sw_version=(
                f"{info.firmware_version} {info.firmware_released}".strip()
                if info
                else None
            ),
            serial_number=info.serial_number if info else None,
        )

    def redact(self, value: str) -> str:
        """Apply the masking option to a card/employee identifier."""
        if self.coordinator.config_entry.options.get(
            OPT_MASK_CARD_DATA, DEFAULT_MASK_CARD_DATA
        ):
            return mask_identifier(value)
        return value
