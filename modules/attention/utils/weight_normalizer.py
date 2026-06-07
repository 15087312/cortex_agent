"""
权重归一化工具
"""


class WeightNormalizer:
    """权重归一化器"""
    
    @staticmethod
    def normalize(weights: list) -> list:
        """归一化权重"""
        total = sum(weights)
        if total == 0:
            return weights
        return [w / total for w in weights]
