from __future__ import annotations

from fastmcp import FastMCP
from pydantic import BaseModel, Field


mcp = FastMCP("weather")


class WeatherArgs(BaseModel):
    location: str = Field(min_length=1)


@mcp.tool
def get_weather(location: str) -> str:
    """Get demo weather for a location."""
    args = WeatherArgs(location=location)
    return (
        f"The weather in {args.location} is 72 F and sunny. "
        "Source: local demo fixture."
    )


if __name__ == "__main__":
    mcp.run(transport="http", host="localhost", port=8000, path="/mcp")
