# Hoymiles Nimbus - S-Cloud Integration for Home Assistant

This is a **proof of concept** custom component for Home Assistant that integrates with the Hoymiles S-Cloud platform. It provides comprehensive monitoring and control capabilities for solar panel installations connected through Hoymiles microinverters.

## About Nimbus
**Nimbus** (Latin for "cloud") represents the cloud-based nature of this integration with Hoymiles S-Cloud services. Just as a nimbus cloud brings life-giving rain, this integration brings vital solar energy data and control capabilities to your Home Assistant setup, creating a seamless bridge between your solar installation and smart home automation.

> **Disclaimer**:  
> This project is not affiliated with Hoymiles or any other company. It is provided as-is for experimental purposes. Use at your own risk.

---

## Nimbus Features

### Station-Level Monitoring
- **Real-time Power Output**: Monitor current power generation from your entire solar installation
- **Daily Energy Production**: Track total energy produced each day in kWh
- **Station Overview**: Get comprehensive station status and performance metrics

### Individual Solar Panel Monitoring
- **Per-Panel Power Tracking**: Monitor power output from each individual solar panel
- **Voltage & Current Sensors**: Track electrical parameters (voltage and current) for each panel
- **Panel Performance Analysis**: Compare performance across different panels to identify issues
- **Position Mapping**: View panel layout with X/Y coordinates for spatial awareness

### Microinverter Monitoring
- **Grid Voltage, Grid Frequency, Temperature and AC Power** for every microinverter
- **Grid Voltage Max Today**, useful to spot overvoltage problems on the grid
- **Production Dropouts Today**: 5-minute slots where the inverter was producing before and after, had grid voltage, but reported 0 W (typical of grid-protection trips such as overvoltage)

### Intelligent Power Control
- **Dynamic Power Limiting**: Adjust power output percentage of microinverters (5-100%)
- **Smart Throttling**: Automatic rate limiting prevents API overload while ensuring timely updates
- **Delayed Write Protection**: Prevents rapid successive changes that could stress the system

### Enhanced Device Management
- **Unified Device Registry**: Centralized device creation ensures consistent naming across all components
- **Hierarchical Organization**: Solar panels are properly linked to their parent stations
- **Consistent Identifiers**: All entities use standardized naming conventions for better organization

### Advanced System Coordination
- **Shared Data Coordinator**: Efficient data sharing between multiple sensors reduces API calls
- **Configurable Refresh Interval**: one setting (5-60 minutes, default 5) for all sensors. Hoymiles only receives new data from the DTU about every 5 minutes, so faster polling does not give fresher data
- **Error Resilience**: Graceful handling of missing or invalid data points

---

## Nimbus Installation Guide

### 1. **Download the Nimbus Component**
- **HACS**: add this repository under HACS → ⋮ → Custom repositories (type *Integration*) and download **Hoymiles Nimbus**.
- **Manual**: copy the `custom_components/hoymiles_nimbus` folder into your Home Assistant configuration:
   ```
   /config/custom_components/hoymiles_nimbus/
   ```

### 2. **Restart Home Assistant**
- Restart Home Assistant to load the Nimbus custom component.

### 3. **Add the Nimbus Integration**
1. Go to **Settings > Devices & Services** in the Home Assistant UI.
2. Click **Add Integration** and search for "Hoymiles Nimbus".
3. Enter your Hoymiles S-Cloud credentials and the base URL (default: `https://neapi.hoymiles.com/`).
   Both the current S-Cloud login (Argon2, used by recently created accounts) and the legacy login are supported.

### 4. **Configure Nimbus Settings**
- After adding the integration, click **Configure** in **Devices & Services** to change the credentials or the **Refresh interval**.

---

## Using Hoymiles Nimbus

### Available Entities
Once installed, Nimbus creates several types of entities for comprehensive monitoring:

#### Station Entities (Per Installation)
- **`sensor.hoymiles_station_[name]_current_power`** - Real-time power output in watts
- **`sensor.hoymiles_station_[name]_daily_energy`** - Daily energy production in kWh  
- **`number.hoymiles_station_[name]_power_level`** - Power limit control (5-100%)

#### Individual Solar Panel Entities (Per Panel)
- **`sensor.hoymiles_station_[name]_panel_[id]_power`** - Per-panel power output in watts
- **`sensor.hoymiles_station_[name]_panel_[id]_voltage`** - Panel voltage in volts
- **`sensor.hoymiles_station_[name]_panel_[id]_current`** - Panel current in amperes

#### Microinverter Entities (Per Inverter)
- **Grid Voltage**, **Grid Frequency**, **Temperature**, **AC Power**
- **Grid Voltage Max Today** and **Production Dropouts Today** (0 W slots in the middle of the
  production day while grid voltage is present: usually grid-protection trips such as overvoltage)
- Updated every 5 minutes from the S-Cloud day series; the DTU itself uploads every 5-15 minutes

### Device Organization
- All entities are properly grouped under their respective devices in Home Assistant
- Station devices contain the main power/energy sensors and power level controls  
- Individual solar panel devices are linked to their parent station for easy navigation
- Consistent naming ensures all related entities are easily identifiable

---

## Nimbus Limitations
- This is a **proof of concept** and may not handle all edge cases.
- The component relies on the Hoymiles S-Cloud API, which may change or become unavailable without notice.
- API calls follow the configured refresh interval (default 5 minutes).
- Microinverter and panel values come from the same 5-minute day series the S-Cloud website charts use; they are as fresh as the last DTU upload.
- Power level changes are rate-limited to prevent rapid successive modifications that could impact system stability.

---

## Future Nimbus Enhancements
- **Historical Data Analysis**: Add support for retrieving and analyzing historical performance data
- **Advanced Monitoring**: Implement microinverter health monitoring and diagnostic features  
- **Weather Integration**: Correlate solar performance with weather data for predictive analytics
- **Performance Alerts**: Add automated notifications for underperforming panels or system issues
- **Energy Optimization**: Develop intelligent power management based on usage patterns
- **Multi-Site Support**: Enhanced support for installations across multiple locations
- **Improved Error Handling**: More robust error recovery and user feedback mechanisms
- **Broader Test Coverage**: a first test suite lives in `tests/` (`pip install -r requirements_test.txt && pytest`)

---

## Disclaimer
This project is not affiliated with Hoymiles or any other company. It is provided as-is for experimental purposes. Use at your own risk. The author is not responsible for any issues that may arise from using this component.

---

## Feedback
If you encounter any issues or have suggestions for improvement, feel free to open an issue or contribute to the project.