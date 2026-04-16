```python
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import fft, signal
from scipy.optimize import minimize
import warnings
warnings.filterwarnings('ignore')
import os

# 创建输出目录
os.makedirs('E:/mathmodel/vol/outputs/figures', exist_ok=True)

# 全局配置
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

COLORS = {
    'primary': '#2E5B88',
    'secondary': '#E85D4C',
    'tertiary': '#4A9B7F',
    'neutral': '#7F7F7F',
    'light': '#B8D4E8',
}

# ============================================================================
# 1. 生成模拟数据（基于SiC材料特性）
# ============================================================================

def generate_simulated_reflectance(d_true=10.0, noise_level=0.02, n_points=1000):
    """
    生成模拟的SiC外延层反射率数据
    参数：
        d_true: 真实厚度 (μm)
        noise_level: 噪声水平
        n_points: 波长点数
    """
    # 波长范围：2-20 μm (红外波段)
    wavelengths = np.linspace(2.0, 20.0, n_points)  # μm
    
    # SiC的折射率模型 (简化Sellmeier方程)
    # n(λ) = √(A + Bλ²/(λ² - C²))，参数参考文献
    A, B, C = 1.0, 3.5, 0.1  # 简化参数
    
    # 计算波长相关的折射率
    n_epi = np.sqrt(A + B * wavelengths**2 / (wavelengths**2 - C**2))
    n_sub = 2.6  # 衬底折射率 (常数近似)
    
    # 相位差
    delta = 4 * np.pi * n_epi * d_true / wavelengths
    
    # 反射率公式 (简化模型)
    R = 0.1 + 0.15 * np.cos(delta)  # 基础反射率 + 干涉项
    
    # 添加噪声
    noise = np.random.normal(0, noise_level, n_points)
    R_noisy = np.clip(R + noise, 0, 1)
    
    return wavelengths, R_noisy, n_epi, n_sub

# ============================================================================
# 2. 傅里叶变换法 (FFT) 厚度估计
# ============================================================================

def fft_thickness_estimation(wavelengths, R, n_avg=2.6):
    """
    使用FFT方法估计厚度
    参数：
        wavelengths: 波长数组 (μm)
        R: 反射率数组
        n_avg: 平均折射率
    """
    # 转换为波数空间 (1/λ)
    wavenumbers = 1.0 / wavelengths  # μm⁻¹
    
    # 重采样到均匀波数网格
    wavenumber_min = wavenumbers.min()
    wavenumber_max = wavenumbers.max()
    n_points = len(wavelengths)
    wavenumber_uniform = np.linspace(wavenumber_min, wavenumber_max, n_points)
    R_uniform = np.interp(wavenumber_uniform, wavenumbers, R)
    
    # 执行FFT
    fft_result = fft.fft(R_uniform - np.mean(R_uniform))
    fft_freq = fft.fftfreq(n_points, d=(wavenumber_uniform[1] - wavenumber_uniform[0]))
    
    # 取正频率部分
    positive_freq_idx = fft_freq > 0
    fft_freq_positive = fft_freq[positive_freq_idx]
    fft_magnitude = np.abs(fft_result[positive_freq_idx])
    
    # 找到主峰对应的频率
    main_peak_idx = np.argmax(fft_magnitude[1:]) + 1  # 跳过0频率
    peak_freq = fft_freq_positive[main_peak_idx]
    
    # 厚度估计公式：d = m / (2nΔν)，其中m=1对应基频
    d_estimate = 1.0 / (2 * n_avg * abs(peak_freq))
    
    return d_estimate, fft_freq_positive, fft_magnitude, peak_freq

# ============================================================================
# 3. 传输矩阵法 (TMM) 精确拟合
# ============================================================================

def tmm_reflectance(d, wavelengths, n_epi, n_sub=2.6):
    """
    计算给定厚度下的TMM反射率
    简化模型：单层薄膜
    """
    # 相位厚度
    delta = 2 * np.pi * n_epi * d / wavelengths
    
    # 菲涅尔系数
    r01 = (1 - n_epi) / (1 + n_epi)  # 空气-外延层
    r12 = (n_epi - n_sub) / (n_epi + n_sub)  # 外延层-衬底
    
    # 反射率公式
    numerator = r01**2 + r12**2 + 2 * r01 * r12 * np.cos(2 * delta)
    denominator = 1 + (r01 * r12)**2 + 2 * r01 * r12 * np.cos(2 * delta)
    R_tmm = numerator / denominator
    
    return np.abs(R_tmm)

def tmm_fit(d_initial, wavelengths, R_measured, n_epi, n_sub=2.6):
    """
    TMM拟合优化
    """
    def objective(d):
        R_pred = tmm_reflectance(d, wavelengths, n_epi, n_sub)
        return np.sum((R_pred - R_measured)**2)
    
    # 约束：厚度为正
    bounds = [(0.1, 100.0)]
    
    result = minimize(objective, d_initial, bounds=bounds, method='L-BFGS-B')
    
    return result.x[0], result.fun

# ============================================================================
# 4. 敏感性分析函数
# ============================================================================

def sensitivity_analysis_noise(d_true=10.0, noise_levels=None):
    """
    分析噪声水平对厚度估计的影响
    """
    if noise_levels is None:
        noise_levels = np.linspace(0.01, 0.1, 10)
    
    results = []
    
    for noise in noise_levels:
        # 生成带噪声的数据
        wavelengths, R, n_epi, n_sub = generate_simulated_reflectance(
            d_true=d_true, noise_level=noise
        )
        
        # FFT估计
        d_fft, _, _, _ = fft_thickness_estimation(wavelengths, R)
        
        # TMM拟合
        d_tmm, _ = tmm_fit(d_fft, wavelengths, R, n_epi, n_sub)
        
        # 计算误差
        error_fft = abs(d_fft - d_true) / d_true * 100
        error_tmm = abs(d_tmm - d_true) / d_true * 100
        
        results.append({
            'noise_level': noise,
            'd_fft': d_fft,
            'd_tmm': d_tmm,
            'error_fft_pct': error_fft,
            'error_tmm_pct': error_tmm
        })
    
    return pd.DataFrame(results)

def sensitivity_analysis_thickness_range(d_values=None):
    """
    分析不同真实厚度下的估计精度
    """
    if d_values is None:
        d_values = np.linspace(1.0, 50.0, 20)  # 1-50 μm
    
    results = []
    
    for d_true in d_values:
        # 生成数据 (固定噪声水平0.02)
        wavelengths, R, n_epi, n_sub = generate_simulated_reflectance(
            d_true=d_true, noise_level=0.02
        )
        
        # FFT估计
        d_fft, _, _, _ = fft_thickness_estimation(wavelengths, R)
        
        # TMM拟合
        d_tmm, _ = tmm_fit(d_fft, wavelengths, R, n_epi, n_sub)
        
        # 计算误差
        error_fft = abs(d_fft - d_true)
        error_tmm = abs(d_tmm - d_true)
        error_fft_pct = error_fft / d_true * 100
        error_tmm_pct = error_tmm / d_true * 100
        
        results.append({
            'd_true': d_true,
            'd_fft': d_fft,
            'd_tmm': d_tmm,
            'error_fft': error_fft,
            'error_tmm': error_tmm,
            'error_fft_pct': error_fft_pct,
            'error_tmm_pct': error_tmm_pct
        })
    
    return pd.DataFrame(results)

def sensitivity_analysis_refractive_index(d_true=10.0):
    """
    分析折射率不确定性对厚度估计的影响
    """
    # 折射率变化范围 (±10%)
    n_variations = np.linspace(0.9, 1.1, 21)  # 相对变化
    
    results = []
    
    for n_factor in n_variations:
        # 生成数据
        wavelengths, R, n_epi_base, n_sub = generate_simulated_reflectance(
            d_true=d_true, noise_level=0.02
        )
        
        # 应用折射率变化
        n_epi_modified = n_epi_base * n_factor
        
        # 使用错误的折射率进行估计
        d_fft_wrong, _, _, _ = fft_thickness_estimation(wavelengths, R, n_avg=2.6*n_factor)
        
        # TMM拟合 (使用错误的折射率)
        d_tmm_wrong, _ = tmm_fit(d_fft_wrong, wavelengths, R, n_epi_modified, n_sub)
        
        # 计算误差
        error_fft = abs(d_fft_wrong - d_true) / d_true * 100
        error_tmm = abs(d_tmm_wrong - d_true) / d_true * 100
        
        results.append({
            'n_factor': n_factor,
            'n_change_pct': (n_factor - 1) * 100,
            'd_fft': d_fft_wrong,
            'd_tmm': d_tmm_wrong,
            'error_fft_pct': error_fft,
            'error_tmm_pct': error_tmm
        })
    
    return pd.DataFrame(results)

# ============================================================================
# 5. 执行敏感性分析
# ============================================================================

print("=" * 60)
print("开始敏感性分析")
print("=" * 60)

# 5.1 噪声敏感性分析
print("\n【1. 噪声敏感性分析】")
noise_df = sensitivity_analysis_noise(d_true=10.0)
print("噪声敏感性分析结果：")
print(noise_df[['noise_level', 'error_fft_pct', 'error_tmm_pct']].round(4).to_string())

# 5.2 厚度范围敏感性分析
print("\n【2. 厚度范围敏感性分析】")
thickness_df = sensitivity_analysis_thickness_range()
print("厚度范围敏感性分析结果（前5行）：")
print(thickness_df.head().round(4).to_string())

# 5.3 折射率敏感性分析
print("\n【3. 折射率敏感性分析】")
refractive_df = sensitivity_analysis_refractive_index(d_true=10.0)
print("折射率敏感性分析结果（关键点）：")
print(refractive_df.iloc[[0, 5, 10, 15, 20]].round(4).to_string())

# ============================================================================
# 6. 可视化结果
# ============================================================================

# 6.1 噪声敏感性图
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# 子图1：噪声对估计厚度的影响
ax1 = axes[0]
ax1.plot(noise_df['noise_level'], noise_df['d_fft'], 
         marker='o', label='FFT估计', color=COLORS['primary'], linewidth=2)
ax1.plot(noise_df['noise_level'], noise_df['d_tmm'], 
         marker='s', label='TMM拟合', color=COLORS['secondary'], linewidth=2)
ax1.axhline(y=10.0, color='gray', linestyle='--', linewidth=1.5, label='真实厚度 (10 μm)')
ax1.set_xlabel('噪声水平')
ax1.set_ylabel('估计厚度 (μm)')
ax1.set_title('(a) 噪声对厚度估计的影响')
ax1.legend()
ax1.grid(True, alpha=0.3)

# 子图2：噪声对相对误差的影响
ax2 = axes[1]
ax2.plot(noise_df['noise_level'], noise_df['error_fft_pct'], 
         marker='o', label='FFT误差', color=COLORS['primary'], linewidth=2)
ax2.plot(noise_df['noise_level'], noise_df['error_tmm_pct'], 
         marker='s', label='TMM误差', color=COLORS['secondary'], linewidth=2)
ax2.axhline(y=1.0, color='red', linestyle='--', linewidth=1.5, label='1%误差线')
ax2.set_xlabel('噪声水平')
ax2.set_ylabel('相对误差 (%)')
ax2.set_title('(b) 噪声对测量误差的影响')
ax2.legend()
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('E:/mathmodel/vol/outputs/figures/sensitivity_noise.png', dpi=300)
plt.close()

print("\n【图1数据特征 - 噪声敏感性】")
print(f"   噪声范围: {noise_df['noise_level'].min():.3f} 到 {noise_df['noise_level'].max():.3f}")
print(f"   FFT误差范围: {noise_df['error_fft_pct'].min():.2f}% 到 {noise_df['error_fft_pct'].max():.2f}%")
print(f"   TMM误差范围: {noise_df['error_tmm_pct'].min():.2f}% 到 {noise_df['error_tmm_pct'].max():.2f}%")
print(f"   TMM平均改善: {(noise_df['error_fft_pct'].mean() - noise_df['error_tmm_pct'].mean()):.2f}个百分点")

# 6.2 厚度范围敏感性图
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

# 子图1：不同真实厚度下的估计值
ax1 = axes[0]
ax1.plot(thickness_df['d_true'], thickness_df['d_fft'], 
         marker='o', markersize=4, label='FFT估计', color=COLORS['primary'], alpha=0.7)
ax1.plot(thickness_df['d_true'], thickness_df['d_tmm'], 
         marker='s', markersize=4, label='TMM拟合', color=COLORS['secondary'], alpha=0.7)
ax1.plot([0, 50], [0, 50], 'k--', linewidth=1.5, label='理想线', alpha=0.5)
ax1.set_xlabel('真实厚度 (μm)')
ax1.set_ylabel('估计厚度 (μm)')
ax1.set_title('(a) 厚度估计准确性')
ax1.legend()
ax1.grid(True, alpha=0.3)

# 子图2：相对误差随厚度的变化
ax2 = axes[1]
ax2.plot(thickness_df['d_true'], thickness_df['error_fft_pct'], 
         marker='o', markersize=4, label='FFT误差', color=COLORS['primary'], alpha=0.7)
ax2.plot(thickness_df['d_true'], thickness_df['error_tmm_pct'], 
         marker='s', markersize=4, label='TMM误差', color=COLORS['secondary'], alpha=0.7)
ax2.axhline(y=1.0, color='red', linestyle='--', linewidth=1.5, label='1%误差线')
ax2.set_xlabel('真实厚度 (μm)')
ax2.set_ylabel('相对误差 (%)')
ax2.set_title('(b) 厚度对测量误差的影响')
ax2.legend()
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('E:/mathmodel/vol/outputs/figures/sensitivity_thickness.png', dpi=300)
plt.close()

# 6.3 折射率敏感性图
fig, axes = plt.subplots(1, 2, figsize=(12, 5))

#