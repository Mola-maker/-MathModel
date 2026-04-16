```python
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import os
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

# 设置中文字体和图表样式
plt.rcParams.update({
    'font.family': 'Arial',
    'font.size': 11,
    'axes.titlesize': 12,
    'axes.titleweight': 'bold',
    'axes.labelsize': 11,
    'axes.linewidth': 1.2,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'legend.fontsize': 10,
    'legend.frameon': False,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.1,
})
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
sns.set_theme(style='ticks')

# 创建输出目录
output_dir = "E:/mathmodel/vol/outputs/figures/"
os.makedirs(output_dir, exist_ok=True)

# 颜色方案
COLORS = {
    'primary': '#2E5B88',
    'secondary': '#E85D4C',
    'tertiary': '#4A9B7F',
    'neutral': '#7F7F7F',
    'light': '#B8D4E8',
}

print("=" * 60)
print("碳化硅外延层厚度数据 EDA 分析")
print("=" * 60)

# 尝试读取数据文件（假设数据文件在当前目录）
data_files = ['data.csv', 'data.xlsx', '碳化硅数据.csv', 'sic_data.csv', 'B题数据.csv']

data = None
for file in data_files:
    try:
        if file.endswith('.csv'):
            # 尝试多种编码
            for encoding in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                try:
                    data = pd.read_csv(file, encoding=encoding)
                    print(f"成功读取文件: {file} (编码: {encoding})")
                    break
                except:
                    continue
        elif file.endswith('.xlsx'):
            data = pd.read_excel(file)
            print(f"成功读取文件: {file}")
        
        if data is not None and not data.empty:
            break
    except Exception as e:
        continue

if data is None:
    # 如果没有找到数据文件，创建示例数据用于演示
    print("未找到数据文件，创建示例数据用于演示EDA流程")
    np.random.seed(42)
    n_samples = 200
    
    # 创建示例数据：碳化硅外延层厚度相关参数
    data = pd.DataFrame({
        'sample_id': range(1, n_samples + 1),
        'thickness_measured': np.random.normal(10, 2, n_samples).clip(5, 15),  # 测量厚度 (μm)
        'interference_fringes': np.random.poisson(15, n_samples) + np.random.normal(0, 1, n_samples),  # 干涉条纹数
        'wavelength': np.random.uniform(800, 1200, n_samples),  # 波长 (nm)
        'refractive_index': np.random.normal(2.65, 0.05, n_samples),  # 折射率
        'temperature': np.random.normal(300, 10, n_samples),  # 温度 (K)
        'pressure': np.random.normal(1.0, 0.1, n_samples),  # 压力 (atm)
        'growth_time': np.random.uniform(30, 120, n_samples),  # 生长时间 (min)
        'doping_concentration': np.random.lognormal(1, 0.5, n_samples),  # 掺杂浓度 (cm^-3)
        'measurement_error': np.random.exponential(0.2, n_samples)  # 测量误差
    })
    
    # 添加一些缺失值
    for col in data.columns[1:]:
        if np.random.random() < 0.05:
            idx = np.random.choice(data.index, size=int(n_samples * 0.05), replace=False)
            data.loc[idx, col] = np.nan
    
    # 添加一些异常值
    outlier_idx = np.random.choice(data.index, size=5, replace=False)
    data.loc[outlier_idx, 'thickness_measured'] *= 2
    
    print("示例数据创建完成")

print("\n" + "=" * 60)
print("1. 数据概览")
print("=" * 60)

# 查看数据基本信息
print("【数据基本信息】")
print(f"数据形状: {data.shape}")
print(f"行数: {data.shape[0]}, 列数: {data.shape[1]}")
print("\n【数据类型】")
print(data.dtypes)
print("\n【前5行数据】")
print(data.head().to_string())
print("\n【后5行数据】")
print(data.tail().to_string())

# 数据信息概览
print("\n【数据信息摘要】")
data.info()

print("\n" + "=" * 60)
print("2. 缺失值分析")
print("=" * 60)

# 缺失值分析
missing = data.isnull().sum()
missing_pct = (missing / len(data) * 100).round(2)

print("【缺失值报告】")
missing_report = pd.DataFrame({
    '缺失数量': missing,
    '缺失比例(%)': missing_pct
})
print(missing_report[missing_report['缺失数量'] > 0].to_string())

if missing.sum() > 0:
    print("\n【缺失值处理策略】")
    for col in data.columns:
        if missing[col] > 0:
            if missing_pct[col] < 5:
                strategy = "均值/中位数填充"
                fill_value = data[col].median() if data[col].dtype in ['float64', 'int64'] else data[col].mode()[0]
                data[col].fillna(fill_value, inplace=True)
                print(f"  {col}: 缺失率 {missing_pct[col]}% → {strategy} (填充值: {fill_value:.4f})")
            elif missing_pct[col] < 20:
                strategy = "线性插值"
                data[col].interpolate(method='linear', inplace=True)
                print(f"  {col}: 缺失率 {missing_pct[col]}% → {strategy}")
            else:
                strategy = "删除该列"
                print(f"  {col}: 缺失率 {missing_pct[col]}% → ⚠️ {strategy}")
else:
    print("数据完整，无缺失值")

print("\n" + "=" * 60)
print("3. 描述性统计分析")
print("=" * 60)

# 描述性统计
numeric_cols = data.select_dtypes(include=[np.number]).columns
if len(numeric_cols) > 0:
    stats_df = data[numeric_cols].describe().T
    stats_df['median'] = data[numeric_cols].median()
    stats_df['skewness'] = data[numeric_cols].skew()
    stats_df['kurtosis'] = data[numeric_cols].kurtosis()
    
    print("【描述性统计表】（供论文直接引用）")
    display_cols = ['count', 'mean', 'std', 'min', '25%', '50%', '75%', 'max', 'skewness', 'kurtosis']
    print(stats_df[display_cols].round(4).to_string())
    
    # 计算变异系数
    cv = (stats_df['std'] / stats_df['mean']).abs() * 100
    print("\n【变异系数(CV%) - 数据离散程度】")
    for col in numeric_cols:
        print(f"  {col}: {cv[col]:.2f}% ({'高离散' if cv[col] > 30 else '中等离散' if cv[col] > 15 else '低离散'})")

print("\n" + "=" * 60)
print("4. 异常值检测")
print("=" * 60)

# 异常值检测（IQR方法）
if len(numeric_cols) > 0:
    print("【异常值检测报告 - IQR方法】")
    outliers_summary = []
    
    for col in numeric_cols:
        Q1 = data[col].quantile(0.25)
        Q3 = data[col].quantile(0.75)
        IQR = Q3 - Q1
        lower_bound = Q1 - 1.5 * IQR
        upper_bound = Q3 + 1.5 * IQR
        
        outliers = data[(data[col] < lower_bound) | (data[col] > upper_bound)]
        outlier_pct = len(outliers) / len(data) * 100
        
        outliers_summary.append({
            '变量': col,
            '下界': lower_bound,
            '上界': upper_bound,
            '异常值数量': len(outliers),
            '异常值比例(%)': outlier_pct
        })
        
        if len(outliers) > 0:
            print(f"\n  {col}:")
            print(f"    正常范围: [{lower_bound:.4f}, {upper_bound:.4f}]")
            print(f"    异常值: {len(outliers)} 个 ({outlier_pct:.2f}%)")
            print(f"    异常值范围: [{outliers[col].min():.4f}, {outliers[col].max():.4f}]")
    
    outliers_df = pd.DataFrame(outliers_summary)
    print("\n【异常值汇总】")
    print(outliers_df.to_string(index=False))

print("\n" + "=" * 60)
print("5. 数据分布可视化")
print("=" * 60)

# 数据分布可视化
if len(numeric_cols) > 0:
    n_cols = min(3, len(numeric_cols))
    n_rows = (len(numeric_cols) + n_cols - 1) // n_cols
    
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(15, 4*n_rows))
    if n_rows > 1:
        axes = axes.flatten()
    elif n_cols > 1:
        axes = axes
    else:
        axes = [axes]
    
    for idx, col in enumerate(numeric_cols):
        if idx < len(axes):
            ax = axes[idx]
            # 直方图 + KDE
            sns.histplot(data=data, x=col, kde=True, color=COLORS['primary'], 
                        alpha=0.6, ax=ax, stat='density')
            
            # 添加正态分布参考线
            if len(data[col].dropna()) > 1:
                mu, std = data[col].mean(), data[col].std()
                xmin, xmax = ax.get_xlim()
                x = np.linspace(xmin, xmax, 100)
                p = stats.norm.pdf(x, mu, std)
                ax.plot(x, p, 'k', linewidth=1.5, alpha=0.7, label='正态分布')
            
            ax.set_title(f'{col} 分布', fontsize=12, fontweight='bold')
            ax.set_xlabel(col)
            ax.set_ylabel('密度')
            ax.legend()
            
            # 在子图中标注统计信息
            if col in stats_df.index:
                skewness = stats_df.loc[col, 'skewness']
                textstr = f'偏度: {skewness:.2f}\n峰度: {stats_df.loc[col, "kurtosis"]:.2f}'
                ax.text(0.05, 0.95, textstr, transform=ax.transAxes, fontsize=9,
                       verticalalignment='top', bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
    
    # 隐藏多余的子图
    for idx in range(len(numeric_cols), len(axes)):
        axes[idx].set_visible(False)
    
    plt.tight_layout()
    dist_path = os.path.join(output_dir, 'data_distribution.png')
    plt.savefig(dist_path, dpi=300)
    plt.close()
    
    print(f"数据分布图已保存: {dist_path}")
    
    # 输出分布特征
    print("\n【图1数据特征 - 数据分布】")
    for col in numeric_cols[:5]:  # 只显示前5个变量的特征
        if col in stats_df.index:
            skew = stats_df.loc[col, 'skewness']
            kurt = stats_df.loc[col, 'kurtosis']
            dist_type = "正态" if abs(skew) < 0.5 and abs(kurt) < 1 else "右偏" if skew > 0.5 else "左偏" if skew < -0.5 else "尖峰" if kurt > 1 else "平峰"
            print(f"  {col}: 偏度={skew:.3f}, 峰度={kurt:.3f} → {dist_type}分布")

print("\n" + "=" * 60)
print("6. 箱线图分析（异常值可视化）")
print("=" * 60)

# 箱线图分析
if len(numeric_cols) > 0:
    fig, axes = plt.subplots(1, min(5, len(numeric_cols)), figsize=(15, 6))
    if len(numeric_cols) > 1:
        axes = axes.flatten()
    else:
        axes = [axes]
    
    for idx, col in enumerate(numeric_cols[:len(axes)]):
        ax = axes[idx]
        bp = ax.boxplot(data[col].dropna(), patch_artist=True)
        
        # 设置箱线图颜色
        bp['boxes'][0].set_facecolor(COLORS['light'])
        bp['boxes'][0].set_alpha(0.7)
        bp['medians'][0].set_color(COLORS['secondary'])
        bp['medians'][0].set_linewidth(2)
        bp['whiskers'][0].set_color(COLORS['primary'])
        bp['whiskers'][1].set_color(COLORS['primary'])
        bp['caps'][0].set_color(COLORS['primary'])
        bp['caps'][1].set_color(COLORS['primary'])
        bp['fliers'][0].set_markerfacecolor(COLORS['secondary'])
        bp['fliers'][0].set_markeredgecolor(COLORS['secondary'])
        
        ax.set_title(f'{col} 箱线图', fontsize=12, fontweight='bold')
        ax.set_ylabel('数值')
        
        # 标注异常值数量
        Q1 = data[col].quantile(0.25)
        Q3 = data[col].quantile(0.75)
        IQR = Q3 - Q1
        outliers = data[(data[col] < Q1 - 1.5*IQR) | (data[col] > Q3 + 1.5*IQR)]
        ax.text(0.05, 0.95, f'异常值: {len(outliers)}个', transform=ax.transAxes,
               fontsize=10, verticalalignment='top',
               bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.3))
    
    plt.tight_layout()
    boxplot_path = os.path.join(output_dir, 'boxplot_analysis.png')
    plt.savefig(boxplot_path, dpi=300)
    plt.close()
    
    print(f"箱线图已保存: {boxplot_path}")
    
    # 输出箱线图特征
    print("\n【图2数据特征 - 箱线图分析】")
    for col in numeric_cols[:3]:  # 只显示前3个变量的特征
        Q1 = data[col].quantile(0.25)
        Q3 = data[col].quantile(0.75)
        median = data[col].median()
        IQR = Q3 - Q1
        outliers = data[(data[col] < Q1 - 1.5*IQR) | (data[col] > Q3 + 1.5*IQR)]
        print(f"  {col}: 中位数={median:.3f}, IQR={IQR:.3f}, 异常值={len(outliers)}个")

print("\n" + "=" * 60)
print("7. 相关性分析")
print("=" * 60)

# 相关性分析
if len(numeric_cols) > 1:
    # 计算相关系数矩阵
    corr_matrix = data[numeric_cols].corr(method='pearson')
    
    print("【Pearson相关系数矩阵】")
    print(corr_matrix.round(3).to_string())
    
    # 找出最强相关关系
    corr_flat = corr_matrix.unstack()
    corr_flat = corr_flat[corr_flat.index.get_level_values(0) != corr_flat.index.get_level_values(1)]
    
    max_corr = corr_flat.abs().max()
    min_corr = corr_flat.abs().min()
    
    strongest_pos = corr_flat[corr_flat == corr_flat.max()].index[0]
    strongest_neg = corr_flat[corr_flat == corr_flat.min()].index[0]
    
    print(f"\n【最强相关关系】")
    print(f"  最强正相关: {strongest_pos[0]} vs {strongest_pos[1]} (r={corr_matrix.loc[strongest_pos[0], strongest_pos[1]]:.3f})")
    print(f"  最强负相关: {strongest_neg[0]} vs {strongest_neg[1]} (r={corr_matrix.loc[strongest_neg[0], strongest_