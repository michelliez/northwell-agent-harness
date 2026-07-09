from __future__ import annotations

from fastmcp import FastMCP
from pydantic import BaseModel, Field


mcp = FastMCP("weather")


class WeatherArgs(BaseModel):
    location: str = Field(min_length=1)


@mcp.tool
def get_weather(location: str) -> dict[str, object]:
    """
    Get a dummy current weather report for a location.

    You can this tool when the user asks about weather, forecast, temperature,
    rain, snow, humidity, or current sky conditions for a specific place.
    This is dummy data for an MCP integration spike, not a live weather API.
    """
    clean_location = location.strip() or "unknown"
    args = WeatherArgs(location=clean_location)
    return {
        "location": args.location,
        "condition": "sunny",
        "temperature_f": 72,
        "humidity_percent": 45,
        "wind_mph": 6,
        "source": "dummy_mcp_weather_server",
        "is_dummy": True,
    }


if __name__ == "__main__":
    mcp.run(transport="http", host="localhost", port=8000, path="/mcp")
