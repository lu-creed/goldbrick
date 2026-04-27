# GoldBrick 自定义指标 DSL 参考手册

自定义指标使用 JSON 公式树描述计算逻辑。每条子线（sub_indicator）有一个 `formula` 字段，
值是一个嵌套 JSON 对象，每个节点包含 `"op"` 字段声明操作类型，其余字段为该操作的参数。

---

## 一、公式节点（op）完整列表

### 1. 基础值节点

| op | 说明 | 必填字段 | 返回值 | 示例 |
|---|---|---|---|---|
| `num` | 数字常量 | `value: number` | 常量本身 | `{"op":"num","value":20}` |
| `param` | 引用本指标参数 | `name: string` | 参数当前数值 | `{"op":"param","name":"N"}` |
| `intrinsic` | 引用行情字段 | `field: string` | 当日字段值 | `{"op":"intrinsic","field":"close"}` |

**`intrinsic` 支持的 `field` 值：**

| field | 说明 |
|---|---|
| `close` | 收盘价 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `volume` | 成交量（手） |
| `amount` | 成交额（元） |
| `turnover_rate` | 换手率（%） |

---

### 2. 窗口统计节点

| op | 说明 | 必填字段 | 返回值 |
|---|---|---|---|
| `rolling` | 对行情字段做 N 日滚动统计（avg/min/max） | `field`, `n_param`, `stat` | 统计结果 |
| `pct_chg` | 对行情字段计算 N 日涨跌幅（%） | `field`, `n_param` | `(cur - prev) / prev × 100` |
| `highest` | 对子公式计算 N 日内最大值 | `x: formula`, `n_param` | N 日最高值 |
| `lowest` | 对子公式计算 N 日内最小值 | `x: formula`, `n_param` | N 日最低值 |
| `count_if` | 统计 N 日内条件公式不为 0 的天数 | `cond: formula`, `n_param` | 满足条件的天数（整数） |

**`rolling` 的 `stat` 取值：**

| stat | 说明 |
|---|---|
| `avg` | 算术平均（默认，等价于动态 MA） |
| `min` | 最小值 |
| `max` | 最大值 |

**示例：**

```json
// 20 日均价
{"op":"rolling","field":"close","n_param":"N","stat":"avg"}

// 1 日涨跌幅（%）
{"op":"pct_chg","field":"close","n_param":"N"}

// 60 日内最高收盘价
{"op":"highest","x":{"op":"intrinsic","field":"close"},"n_param":"N"}

// N 日内收盘价 > 开盘价的天数
{"op":"count_if","cond":{"op":"sub","left":{"op":"intrinsic","field":"close"},"right":{"op":"intrinsic","field":"open"}},"n_param":"N"}
```

---

### 3. 单目运算节点

| op | 说明 | 必填字段 | 返回值 |
|---|---|---|---|
| `neg` | 取负 | `x: formula` | `-x` |
| `sqrt` | 平方根（负数返回空） | `x: formula` | `√x` |

---

### 4. 四则运算节点

| op | 说明 | 必填字段 | 返回值 |
|---|---|---|---|
| `add` | 加法 | `left: formula`, `right: formula` | `left + right` |
| `sub` | 减法 | `left: formula`, `right: formula` | `left - right` |
| `mul` | 乘法 | `left: formula`, `right: formula` | `left × right` |
| `div` | 除法（除数为 0 时返回空） | `left: formula`, `right: formula` | `left ÷ right` |

---

### 5. 金叉/死叉信号节点

| op | 说明 | 必填字段 | 返回值 |
|---|---|---|---|
| `cross_above` | 快线从下方穿越慢线（金叉） | `fast: formula`, `slow: formula` | `1.0`（当日金叉），否则 `0.0` |
| `cross_below` | 快线从上方穿越慢线（死叉） | `fast: formula`, `slow: formula` | `1.0`（当日死叉），否则 `0.0` |

**判断逻辑：**
- `cross_above`：`fast[i] > slow[i]` 且 `fast[i-1] <= slow[i-1]`
- `cross_below`：`fast[i] < slow[i]` 且 `fast[i-1] >= slow[i-1]`

**示例：5 日均线金叉 10 日均线：**

```json
{
  "op": "cross_above",
  "fast": {"op":"rolling","field":"close","n_param":"Fast","stat":"avg"},
  "slow": {"op":"rolling","field":"close","n_param":"Slow","stat":"avg"}
}
```

---

### 6. 引用节点

| op | 说明 | 必填字段 | 返回值 |
|---|---|---|---|
| `ref_builtin` | 引用系统内置指标子线 | `sub_name: string`, `fetch: FetchSpec` | 指定位置的内置指标值 |
| `ref_sibling` | 引用本指标的另一条已计算子线 | `sub_key: string`, `fetch: FetchSpec` | 指定位置的兄弟子线值 |

**`fetch` 取数方式（FetchSpec）：**

| mode | 说明 | 额外字段 |
|---|---|---|
| `current` | 当前 bar 的值 | 无 |
| `prev_n` | 前 N 个 bar 的值 | `n_param: string` |
| `range` + `avg/min/max` | [i-N+1..i] 区间聚合 | `n_param`, `range_agg` |
| `range` + `std` | [i-N+1..i] 区间标准差 | `n_param`, `range_agg:"std"`, `std_baseline: formula`, `std_volatility: field` |

---

## 二、内置指标子线（ref_builtin sub_name 白名单）

以下子线名称可用于 `ref_builtin` 的 `sub_name` 字段。

### MA（移动平均）
`ma5` · `ma10` · `ma20` · `ma30` · `ma60` · `ma120` · `ma250`

### EMA（指数移动平均）
`ema5` · `ema10` · `ema20` · `ema30` · `ema60`

### BOLL（布林带）
`boll_upper` · `boll_mid` · `boll_lower`

### MACD（指数平滑异同移动平均）
`macd_dif` · `macd_dea` · `macd_bar`（MACD 柱 = DIF - DEA）

### KDJ（随机指标）
`kdj_k` · `kdj_d` · `kdj_j`

### RSI（相对强弱指标）
`rsi6` · `rsi12` · `rsi24`

### ATR（平均真实波动幅度）
`atr14`

### WR（威廉指标）
`wr10` · `wr6`

### CCI（商品通道指数）
`cci14`

### BIAS（乖离率）
`bias6` · `bias12` · `bias24`

### ROC（变动率指标）
`roc12` · `roc6`

### PSY（心理线）
`psy12` · `psy6`

### OBV（能量潮）
`obv`

### DMA（不同周期均线差）
`dma_dif` · `dma_ama`

### TRIX（三重指数平滑移动平均）
`trix12` · `trix_matrix`（TRIX 信号线）

### DMI（趋向指标）
`dmi_pdi` · `dmi_mdi` · `dmi_adx` · `dmi_adxr`

### STDDEV（标准差）
`stddev10` · `stddev20`

### ARBR（人气意愿指标）
`ar26` · `br26`

### VMA（量移动平均）
`vma5` · `vma10` · `vma20`

---

## 三、完整指标定义 JSON 结构

```json
{
  "version": 1,
  "params": [
    {"name": "N", "default_value": "20", "label": "周期"}
  ],
  "periods": ["1d"],
  "sub_indicators": [
    {
      "key": "result",
      "name": "指标名称",
      "formula": { "op": "...", ... },
      "auxiliary_only": false,
      "use_in_screening": true,
      "use_in_chart": true,
      "chart_kind": "line",
      "initial_value": null
    }
  ]
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | `1` | 固定为 1 |
| `params` | 数组 | 用户可调节的参数列表；`name` 在公式中用 `param` 节点引用 |
| `periods` | 字符串数组 | 支持周期：`1d` / `1w` / `1M` / `1Q` / `1y` |
| `sub_indicators` | 数组 | 子线列表，至少 1 条；按声明顺序可引用前面已算好的子线（ref_sibling） |
| `key` | 字符串 | 小写字母开头，仅含小写字母/数字/下划线，最长 64 字符 |
| `auxiliary_only` | bool | 仅辅助计算，不参与选股/图形 |
| `use_in_screening` | bool | 参与条件选股 |
| `use_in_chart` | bool | 参与副图展示（须同时设置 `chart_kind`） |
| `chart_kind` | `line`/`bar` | 副图绘制方式 |
| `initial_value` | 数字或 null | 第一根 K 线计算失败时的兜底初始值 |

---

## 四、典型公式示例

### 示例 1：自定义动态均线差

```json
{
  "version": 1,
  "params": [
    {"name": "Fast", "default_value": "5"},
    {"name": "Slow", "default_value": "20"}
  ],
  "periods": ["1d"],
  "sub_indicators": [
    {
      "key": "fast_ma",
      "name": "快线",
      "formula": {"op":"rolling","field":"close","n_param":"Fast","stat":"avg"},
      "auxiliary_only": true
    },
    {
      "key": "slow_ma",
      "name": "慢线",
      "formula": {"op":"rolling","field":"close","n_param":"Slow","stat":"avg"},
      "auxiliary_only": true
    },
    {
      "key": "diff",
      "name": "均线差",
      "formula": {
        "op": "sub",
        "left": {"op":"ref_sibling","sub_key":"fast_ma","fetch":{"mode":"current"}},
        "right": {"op":"ref_sibling","sub_key":"slow_ma","fetch":{"mode":"current"}}
      },
      "use_in_chart": true,
      "chart_kind": "line"
    }
  ]
}
```

### 示例 2：N 日涨跌幅选股

```json
{
  "version": 1,
  "params": [{"name": "N", "default_value": "5"}],
  "periods": ["1d"],
  "sub_indicators": [
    {
      "key": "chg",
      "name": "N日涨跌幅",
      "formula": {"op":"pct_chg","field":"close","n_param":"N"},
      "use_in_screening": true,
      "use_in_chart": true,
      "chart_kind": "bar"
    }
  ]
}
```

### 示例 3：金叉信号

```json
{
  "version": 1,
  "params": [
    {"name": "Fast", "default_value": "5"},
    {"name": "Slow", "default_value": "20"}
  ],
  "periods": ["1d"],
  "sub_indicators": [
    {
      "key": "signal",
      "name": "金叉信号",
      "formula": {
        "op": "cross_above",
        "fast": {"op":"rolling","field":"close","n_param":"Fast","stat":"avg"},
        "slow": {"op":"rolling","field":"close","n_param":"Slow","stat":"avg"}
      },
      "use_in_screening": true,
      "use_in_chart": true,
      "chart_kind": "bar"
    }
  ]
}
```

### 示例 4：N 日内上涨天数

```json
{
  "version": 1,
  "params": [{"name": "N", "default_value": "10"}],
  "periods": ["1d"],
  "sub_indicators": [
    {
      "key": "up_days",
      "name": "N日上涨天数",
      "formula": {
        "op": "count_if",
        "cond": {
          "op": "sub",
          "left": {"op":"intrinsic","field":"close"},
          "right": {"op":"intrinsic","field":"open"}
        },
        "n_param": "N"
      },
      "use_in_screening": true,
      "use_in_chart": true,
      "chart_kind": "bar"
    }
  ]
}
```
