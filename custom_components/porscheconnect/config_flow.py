"""Config flow for Porsche Connect integration."""

from __future__ import annotations

import base64
import logging

import voluptuous as vol
from homeassistant import exceptions
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import selector

try:  # HA 2025.8+: reloads the entry only when the options really change
    from homeassistant.config_entries import OptionsFlowWithReload as _OptionsFlowBase
except ImportError:  # pragma: no cover - older cores
    from homeassistant.config_entries import OptionsFlow as _OptionsFlowBase
from .pyporscheconnectapi.connection import Connection
from .pyporscheconnectapi.exceptions import (
    PorscheCaptchaRequiredError,
    PorscheExceptionError,
    PorscheWrongCredentialsError,
)
from .pyporscheconnectapi.oauth2 import LOGIN_BACKOFF

from .const import (
    CONF_CAPTCHA_CODE,
    CONF_CODE_VERIFIER,
    CONF_OAUTH_STATE,
    DOMAIN,
    TRANSIENT_AUTH_FIELDS,
)
from .lease import (
    CONF_ANNUAL_KM,
    CONF_EXCESS_PRICE,
    CONF_LEASE,
    CONF_START_DATE,
    CONF_START_KM,
    CONF_TERM_MONTHS,
    parse_config,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({CONF_EMAIL: str, CONF_PASSWORD: str})
CHANGE_PASSWORD_SCHEMA = vol.Schema({CONF_PASSWORD: str})


async def validate_input(data):
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    token = {}
    # A human is driving this flow (new login, captcha, password change):
    # never block it with the automatic login backoff.
    LOGIN_BACKOFF.reset(data[CONF_EMAIL])
    try:
        conn = Connection(
            email=data[CONF_EMAIL],
            password=data[CONF_PASSWORD],
            captcha_code=data.get(CONF_CAPTCHA_CODE),
            state=data.get(CONF_OAUTH_STATE),
            code_verifier=data.get(CONF_CODE_VERIFIER),
            token=token,
            cookies=data.get("cookies"),
        )
    except PorscheExceptionError as exc:
        _LOGGER.debug("Exception %s", exc)

    _LOGGER.debug("Attempting login")
    try:
        token = await conn.get_token()
    except PorscheCaptchaRequiredError as exc:
        _LOGGER.info("Captcha required to log in: %s", exc.captcha)
        return {
            "email": data[CONF_EMAIL],
            "password": data[CONF_PASSWORD],
            "captcha": exc.captcha,
            CONF_OAUTH_STATE: exc.state,
            CONF_CODE_VERIFIER: exc.code_verifier,
            "cookies": exc.cookies,
        }
    except PorscheWrongCredentialsError as exc:
        _LOGGER.info("Wrong credentials.")
        raise InvalidAuth from exc
    except PorscheExceptionError as exc:
        _LOGGER.info("Authentication flow error: %s", exc.message)
        raise InvalidAuth from exc
    except Exception as exc:
        _LOGGER.info("Login failed: %s", exc)
        raise InvalidAuth from exc

    return {"title": data[CONF_EMAIL], CONF_ACCESS_TOKEN: token}


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Porsche Connect."""

    VERSION = 2

    email = None
    password = None
    captcha = None
    state = None
    code_verifier = None
    cookies = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PorscheOptionsFlow:
        """Options: lease mileage tracking per vehicle."""
        return PorscheOptionsFlow()

    @callback
    def _get_entry_for_current_flow(self):
        """Return the existing entry for reauth/reconfigure flows."""
        if self.source == SOURCE_REAUTH:
            return self._get_reauth_entry()
        if self.source == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return None

    @callback
    def _get_expected_unique_id(self) -> str | None:
        """Return the account identifier for the current flow.

        Older entries may not have a unique_id yet, so fall back to the stored email.
        """
        if entry := self._get_entry_for_current_flow():
            return entry.unique_id or entry.data.get(CONF_EMAIL)
        return self.unique_id

    @callback
    def _abort_if_account_mismatch(self) -> None:
        """Abort if reauth/reconfigure targets a different account."""
        expected_unique_id = self._get_expected_unique_id()
        if expected_unique_id is None or self.unique_id == expected_unique_id:
            return
        self._abort_if_unique_id_mismatch()

    @callback
    def _get_entry_data(self, email: str, password: str, access_token) -> dict:
        """Build persistent config entry data without transient auth fields."""
        entry = self._get_entry_for_current_flow()
        entry_data = dict(entry.data) if entry else {}
        for field in TRANSIENT_AUTH_FIELDS:
            entry_data.pop(field, None)
        entry_data.update(
            {
                CONF_EMAIL: email,
                CONF_PASSWORD: password,
                CONF_ACCESS_TOKEN: access_token,
            },
        )
        return entry_data

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
            )

        errors = {}
        _LOGGER.debug("Validating input.")

        unique_id = f"{user_input[CONF_EMAIL]}"
        await self.async_set_unique_id(unique_id)

        try:
            info = await validate_input(user_input)
            if info.get("captcha") and info.get("state"):
                self.email = info.get("email")
                self.password = info.get("password")
                self.captcha = info.get("captcha")
                self.state = info.get("state")
                self.code_verifier = info.get("code_verifier")
                self.cookies = info.get("cookies")
                return self._async_form_captcha()
            entry_data = self._get_entry_data(
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                info.get(CONF_ACCESS_TOKEN),
            )

            if self.source == SOURCE_REAUTH:
                self._abort_if_account_mismatch()
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    unique_id=self.unique_id,
                    data=entry_data,
                )

            if self.source == SOURCE_RECONFIGURE:
                self._abort_if_account_mismatch()
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(),
                    unique_id=self.unique_id,
                    data=entry_data,
                )

            return self.async_create_entry(
                title=info["title"],
                data=entry_data,
            )

        except InvalidAuth:
            errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, user_input=None) -> ConfigFlowResult:
        """Handle configuration by re-auth."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"],
        )
        await self.async_set_unique_id(
            self._get_expected_unique_id(),
            raise_on_progress=False,
        )
        return await self.async_step_user()

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Handle a reconfiguration flow initialized by the user."""
        self._existing_entry_data = dict(self._get_reconfigure_entry().data)
        await self.async_set_unique_id(
            self._get_expected_unique_id(),
            raise_on_progress=False,
        )
        return await self.async_step_change_password()

    async def async_step_change_password(self, user_input=None) -> ConfigFlowResult:
        """Show the change password step."""
        if user_input is not None:
            return await self.async_step_user(
                {
                    CONF_EMAIL: self._existing_entry_data[CONF_EMAIL],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                },
            )

        return self.async_show_form(
            step_id="change_password",
            data_schema=CHANGE_PASSWORD_SCHEMA,
            description_placeholders={
                CONF_EMAIL: self._existing_entry_data[CONF_EMAIL],
            },
        )

    async def async_step_captcha(
        self,
        user_input: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Captcha verification step."""
        if user_input is not None:
            user_input = {
                CONF_EMAIL: self.email,
                CONF_PASSWORD: self.password,
                CONF_CAPTCHA_CODE: user_input[CONF_CAPTCHA_CODE],
                CONF_OAUTH_STATE: self.state,
                CONF_CODE_VERIFIER: self.code_verifier,
                "cookies": self.cookies,
            }
            errors = {}
            try:
                info = await validate_input(user_input)

                # Handle case where another captcha is required
                if info.get("captcha") and info.get("state"):
                    self.captcha = info.get("captcha")
                    self.state = info.get("state")
                    self.code_verifier = info.get("code_verifier")
                    self.cookies = info.get("cookies")
                    return self._async_form_captcha()

                entry_data = self._get_entry_data(
                    user_input[CONF_EMAIL],
                    user_input[CONF_PASSWORD],
                    info.get(CONF_ACCESS_TOKEN),
                )

                if self.source == SOURCE_REAUTH:
                    self._abort_if_account_mismatch()
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(),
                        unique_id=self.unique_id,
                        data=entry_data,
                    )

                if self.source == SOURCE_RECONFIGURE:
                    self._abort_if_account_mismatch()
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(),
                        unique_id=self.unique_id,
                        data=entry_data,
                    )

                return self.async_create_entry(
                    title=info["title"],
                    data=entry_data,
                )

            except InvalidAuth:
                # The captcha/login transaction is used up after a failed attempt:
                # showing the same image again can never succeed. Start over so
                # the next attempt gets a fresh Auth0 transaction and captcha.
                errors["base"] = "invalid_auth"
                return self.async_show_form(
                    step_id="user",
                    data_schema=STEP_USER_DATA_SCHEMA,
                    errors=errors,
                )

        return self._async_form_captcha()

    @callback
    def _async_form_captcha(
        self,
    ) -> ConfigFlowResult:
        """Captcha verification form."""
        # We edit the SVG for better visibility

        (header, payload) = self.captcha.split(",")
        svg = base64.b64decode(payload)
        svg = svg.replace(
            b'width="150" height="50"',
            b'width="300" height="100" style="background-color:white"',
        )
        payload = base64.b64encode(svg)
        self.captcha = header + "," + payload.decode("ascii")

        return self.async_show_form(
            step_id="captcha",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CAPTCHA_CODE, default=vol.UNDEFINED): str,
                },
            ),
            description_placeholders={
                "captcha_img": '<img src="' + self.captcha + '" />',
            },
        )


CONF_VIN = "vin"
CONF_REMOVE = "remove"
_KM = selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=1_000_000, step=1, mode="box", unit_of_measurement="km"))


class PorscheOptionsFlow(_OptionsFlowBase):
    """Configure the lease (km allowance) of each vehicle."""

    vin: str | None = None

    def _vehicles(self) -> dict[str, str]:
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if coordinator is None:
            return {}
        return {v.vin: v.data.get("name", v.vin) for v in coordinator.vehicles}

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Pick the vehicle (skipped when the account has only one)."""
        vehicles = self._vehicles()
        if not vehicles:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self.vin = user_input[CONF_VIN]
            return await self.async_step_lease()
        if len(vehicles) == 1:
            self.vin = next(iter(vehicles))
            return await self.async_step_lease()
        options = [selector.SelectOptionDict(value=vin, label=name) for vin, name in vehicles.items()]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_VIN): selector.SelectSelector(selector.SelectSelectorConfig(options=options))},
            ),
        )

    async def async_step_lease(self, user_input=None) -> ConfigFlowResult:
        """Lease start, odometer at start, yearly allowance, term and optional price per extra km."""
        leases = dict(self.config_entry.options.get(CONF_LEASE, {}))
        errors = {}
        if user_input is not None:
            if user_input.get(CONF_REMOVE):
                leases.pop(self.vin, None)
                await self._async_store(remove=True)
                return self.async_create_entry(data={**self.config_entry.options, CONF_LEASE: leases})
            cfg = {key: value for key, value in user_input.items() if key != CONF_REMOVE}
            if parse_config(cfg) is None:
                errors["base"] = "invalid_lease"
            else:
                leases[self.vin] = cfg
                await self._async_store(cfg)
                return self.async_create_entry(data={**self.config_entry.options, CONF_LEASE: leases})

        current = user_input or leases.get(self.vin, {})

        def default(key):
            return {"suggested_value": current[key]} if current.get(key) not in (None, "") else {}

        schema = vol.Schema(
            {
                vol.Required(CONF_START_DATE, description=default(CONF_START_DATE)): selector.DateSelector(),
                vol.Required(CONF_START_KM, description=default(CONF_START_KM)): _KM,
                vol.Required(CONF_ANNUAL_KM, description=default(CONF_ANNUAL_KM)): _KM,
                vol.Required(CONF_TERM_MONTHS, description=default(CONF_TERM_MONTHS)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=120, step=1, mode="box"),
                ),
                vol.Optional(CONF_EXCESS_PRICE, description=default(CONF_EXCESS_PRICE)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=10, step=0.001, mode="box"),
                ),
                vol.Optional(CONF_REMOVE, default=False): selector.BooleanSelector(),
            },
        )
        return self.async_show_form(
            step_id="lease",
            data_schema=schema,
            errors=errors,
            description_placeholders={"vehicle": self._vehicles().get(self.vin, self.vin)},
        )

    async def _async_store(self, cfg: dict | None = None, *, remove: bool = False) -> None:
        """Mirror the lease into the integration store (survives deleting and re-adding the account)."""
        from . import lease_store

        store = lease_store(self.hass)
        data = await store.async_load() or {}
        if remove:
            data.pop(self.vin, None)
        else:
            data[self.vin] = cfg
        await store.async_save(data)


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""


class CaptchaRequired(exceptions.HomeAssistantError):
    """Error to indicate captcha verification is required."""
