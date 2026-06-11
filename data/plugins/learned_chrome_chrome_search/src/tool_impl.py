"""自动生成的已学工具实现: chrome_search (app: Chrome)"""
from modules.toolbuilder.recipe_engine import RecipeEngine


def execute_chrome_search(**kwargs) -> dict:
    """
    执行已学工具 chrome_search (app: Chrome)

    参数: ['type', 'properties']
    """
    return RecipeEngine.execute("chrome_search", kwargs, "Chrome")
