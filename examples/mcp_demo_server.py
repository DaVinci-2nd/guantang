from mcp.server import MCPServer

server = MCPServer(name="灌汤演示服务器", version="0.1.0")


@server.tool(
    name="query_soup_dumpling_shop",
    description="Query the opening hours and signature dishes of a soup dumpling shop",
)
async def query_soup_dumpling_shop(shop_name: str) -> str:
    return f"{shop_name}：早上 9 点开门，招牌是鲜肉灌汤包。"


@server.tool(
    name="calculate_dumpling_cost",
    description="Calculate the total cost for buying some steamers of soup dumplings",
)
async def calculate_dumpling_cost(steamer_count: int, price_per_steamer: float) -> str:
    total = steamer_count * price_per_steamer
    return f"共 {steamer_count} 笼，每笼 {price_per_steamer} 元，合计 {total} 元。"


if __name__ == "__main__":
    server.run()
