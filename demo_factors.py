import sys
sys.stdout.reconfigure(encoding='utf-8')

# 研报中常见的驱动因子模板
FACTOR_TYPES = {
    '产能': {
        'keywords': ['产能', '产量', '利用率', '扩产', '生产线', '项目落地'],
        'unit_examples': ['万吨/年', '万平', 'GW', '万吨', '台/年'],
        'description': '产能规模、扩张计划、产能利用率'
    },
    '订单': {
        'keywords': ['订单', '新签', '在手', '交付', '签约', '中标'],
        'unit_examples': ['亿', '万元', '万订单'],
        'description': '订单量、交付周期、订单覆盖率'
    },
    '报价': {
        'keywords': ['报价', '价格', '单价', '售价', '均价', '牌价'],
        'unit_examples': ['万/吨', '元/瓦', '元/公斤', '元/吨', '万元/吨'],
        'description': '产品单价、价格区间、历史价格'
    },
    '增速': {
        'keywords': ['同比', '环比', '增速', '增长', '增幅', 'YoY', 'QoQ'],
        'unit_examples': ['%', '个百分点'],
        'description': '同比/环比增长率、增速变化'
    },
    '政策': {
        'keywords': ['政策', '补贴', '规划', '标准', '文件', '征求意见'],
        'unit_examples': [],
        'description': '政策文件名称、补贴标准、规划目标'
    },
    '成本': {
        'keywords': ['成本', '毛利', '净利', '利润率', '单位成本'],
        'unit_examples': ['万/吨', '元/', '%'],
        'description': '单位成本、毛利率、净利率'
    },
    '库存': {
        'keywords': ['库存', '周转', '在制品', '存货'],
        'unit_examples': ['万吨', '亿元', '天'],
        'description': '库存量、周转天数、库存周期'
    },
    '销量': {
        'keywords': ['销量', '销售量', '出货', '交付量', '市占率'],
        'unit_examples': ['万辆', '万台', '万/吨', 'GW'],
        'description': '销售数量、市占率、出货量'
    }
}

# 模拟从研报中提取的完整因子示例
demo_factors = [
    # 产能类
    {'type': '产能', 'factor': '现有产能', 'value': 50, 'unit': '万吨/年', 'context': '公司现有三元前驱体产能50万吨/年'},
    {'type': '产能', 'factor': '规划产能', 'value': 80, 'unit': '万吨/年', 'context': '25年规划产能达80万吨/年'},
    {'type': '产能', 'factor': '产能利用率', 'value': 85, 'unit': '%', 'context': '上半年产能利用率85%'},
    
    # 订单类
    {'type': '订单', 'factor': '新签订单', 'value': 120, 'unit': '亿元', 'context': 'Q2新签订单120亿元，环比+30%'},
    {'type': '订单', 'factor': '在手订单', 'value': 350, 'unit': '亿元', 'context': '截至6月底在手订单350亿元'},
    
    # 报价类
    {'type': '报价', 'factor': '碳酸锂报价', 'value': 23.5, 'unit': '万元/吨', 'context': '电池级碳酸锂报价23.5万元/吨'},
    {'type': '报价', 'factor': '硅料价格', 'value': 8.5, 'unit': '万元/吨', 'context': '致密料价格8.5万元/吨'},
    {'type': '报价', 'factor': '组件价格', 'value': 1.78, 'unit': '元/瓦', 'context': '182尺寸组件1.78元/瓦'},
    
    # 增速类
    {'type': '增速', 'factor': '收入同比', 'value': 25.3, 'unit': '%', 'context': '1H24收入同比+25.3%'},
    {'type': '增速', 'factor': '净利润环比', 'value': -8.5, 'unit': '%', 'context': 'Q2净利润环比-8.5%'},
    {'type': '增速', 'factor': '出货量YoY', 'value': 45, 'unit': '%', 'context': '23年出货量YoY +45%'},
    
    # 政策类
    {'type': '政策', 'factor': '补贴标准', 'value': 12600, 'unit': '元/辆', 'context': '2024年新能源乘用车补贴12600元/辆'},
    {'type': '政策', 'factor': '回收目标', 'value': 60, 'unit': '%', 'context': '25年回收率达到60%以上'},
    
    # 成本类
    {'type': '成本', 'factor': '现金成本', 'value': 18.5, 'unit': '万元/吨', 'context': '碳酸锂现金成本18.5万元/吨'},
    {'type': '成本', 'factor': '毛利率', 'value': 32.5, 'unit': '%', 'context': '2Q24综合毛利率32.5%'},
    
    # 库存类
    {'type': '库存', 'factor': '成品库存', 'value': 15, 'unit': '万吨', 'context': '当前成品库存约15万吨'},
    {'type': '库存', 'factor': '周转天数', 'value': 35, 'unit': '天', 'context': '存货周转天数35天'},
    
    # 销量类
    {'type': '销量', 'factor': '电池出货', 'value': 12.5, 'unit': 'GWh', 'context': '2Q24电池出货12.5GWh'},
    {'type': '销量', 'factor': '市占率', 'value': 18.5, 'unit': '%', 'context': '全球动力电池市占率18.5%'},
]

print('=' * 60)
print('研报因子提取示例')
print('=' * 60)
print()

# 按类型分组展示
from collections import defaultdict
by_type = defaultdict(list)
for f in demo_factors:
    by_type[f['type']].append(f)

for ftype, factors in by_type.items():
    print(f'【{ftype}】')
    for f in factors:
        print(f"  {f['factor']}: {f['value']} {f['unit']}")
        print(f"    → {f['context']}")
    print()

print('=' * 60)
print('向量数据库查询示例')
print('=' * 60)
print()
print('1. 结构化过滤查询:')
print("   SELECT * FROM factor_chunks WHERE type='产能' AND value > 100")
print()
print('2. 向量语义查询:')
print("   -- 找产能扩张对价格的影响相关研报")
print("   SELECT r.* FROM factor_vectors fv")
print("   JOIN factor_chunks fc ON fv.chunk_id = fc.chunk_id")
print("   JOIN reports r ON fc.info_code = r.info_code")
print("   WHERE fv.embedding MATCH ? AND fc.type IN ('产能','报价')")
print()
print('3. 组合查询:')
print("   -- 找报价>20万且产能>50万吨的研报")
print("   SELECT fc.*, r.title FROM factor_chunks fc")
print("   JOIN reports r ON fc.info_code = r.info_code")
print("   WHERE (fc.type, fc.value, fc.unit) IN (")
print("     SELECT '报价', value, unit FROM factor_chunks WHERE value > 20 AND unit='万/吨'")
print("   )")
print("   OR (fc.type='产能' AND fc.value > 50)")
