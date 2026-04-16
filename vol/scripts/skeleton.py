import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import signal, optimize, interpolate
from sklearn.preprocessing import StandardScaler
from sklearn.neural_network import MLPRegressor
from sklearn.model_selection import train_test_split
import SALib.sample.sobol as sobol
from SALib.analyze import sobol as sobol_analyze
import pulp

# ==================== 1. 数据预处理模块 ====================
def load_interference_data(filepath):
    """
    加载红外干涉条纹数据
    假设数据格式：波长(nm), 反射强度
    """
    data = pd.read_csv(filepath)
    wavelengths = data['wavelength'].values
    intensities = data['intensity'].values
    return wavelengths, intensities

def preprocess_interference_signal(intensities, window_size=5):
    """
    预处理干涉信号：平滑、去噪、归一化
    """
    # 移动平均平滑
    smoothed = np.convolve(intensities, np.ones(window_size)/window_size, mode='same')
    
    # 归一化到[0,1]
    normalized = (smoothed - np.min(smoothed)) / (np.max(smoothed) - np.min(smoothed))
    
    return normalized

def extract_interference_features(wavelengths, intensities):
    """
    提取干涉条纹特征：峰值位置、峰谷差、周期等
    """
    # 寻找峰值
    peaks, _ = signal.find_peaks(intensities, distance=10, prominence=0.1)
    valleys, _ = signal.find_peaks(-intensities, distance=10, prominence=0.1)
    
    features = {
        'peak_positions': wavelengths[peaks],
        'peak_intensities': intensities[peaks],
        'valley_positions': wavelengths[valleys],
        'valley_intensities': intensities[valleys],
        'peak_count': len(peaks),
        'avg_peak_distance': np.mean(np.diff(wavelengths[peaks])) if len(peaks) > 1 else 0,
        'max_amplitude': np.max(intensities[peaks]) - np.min(intensities[valleys]) if len(valleys) > 0 else 0
    }
    
    return features

# ==================== 2. 物理模型模块 ====================
def interference_model(thickness, wavelengths, n_epi, n_sub, theta=0):
    """
    计算红外干涉的理论模型
    thickness: 外延层厚度 (nm)
    wavelengths: 波长数组 (nm)
    n_epi: 外延层折射率
    n_sub: 衬底折射率
    theta: 入射角 (度)
    """
    theta_rad = np.radians(theta)
    
    # 光程差计算
    delta = 2 * n_epi * thickness * np.cos(theta_rad)
    
    # 相位差
    phi = 2 * np.pi * delta / wavelengths
    
    # 反射系数（简化模型）
    r1 = (n_epi - 1) / (n_epi + 1)  # 空气-外延层界面
    r2 = (n_sub - n_epi) / (n_sub + n_epi)  # 外延层-衬底界面
    
    # 干涉强度
    I = r1**2 + r2**2 + 2 * r1 * r2 * np.cos(phi)
    
    return I

def thickness_from_interference(wavelengths, intensities, n_epi=2.6, n_sub=2.7, initial_guess=1000):
    """
    通过拟合干涉条纹反演外延层厚度
    """
    def error_func(thickness):
        model_intensity = interference_model(thickness, wavelengths, n_epi, n_sub)
        return np.sum((model_intensity - intensities)**2)
    
    result = optimize.minimize(error_func, initial_guess, bounds=[(10, 10000)])
    
    return result.x[0], result.fun

# ==================== 3. 机器学习模块 ====================
def train_thickness_predictor(features, true_thickness):
    """
    训练厚度预测模型（前馈神经网络）
    """
    # 特征工程
    X = np.array([
        [f['peak_count'], f['avg_peak_distance'], f['max_amplitude']]
        for f in features
    ])
    y = np.array(true_thickness)
    
    # 标准化
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    # 划分训练测试集
    X_train, X_test, y_train, y_test = train_test_split(X_scaled, y, test_size=0.2, random_state=42)
    
    # 训练神经网络
    model = MLPRegressor(
        hidden_layer_sizes=(50, 30, 10),
        activation='relu',
        solver='adam',
        max_iter=1000,
        random_state=42
    )
    
    model.fit(X_train, y_train)
    
    # 评估
    train_score = model.score(X_train, y_train)
    test_score = model.score(X_test, y_test)
    
    return model, scaler, {'train_score': train_score, 'test_score': test_score}

# ==================== 4. 不确定性分析模块 ====================
def sensitivity_analysis():
    """
    使用Sobol方法进行全局敏感性分析
    """
    problem = {
        'num_vars': 4,
        'names': ['n_epi', 'n_sub', 'theta', 'noise_level'],
        'bounds': [[2.5, 2.7], [2.6, 2.8], [0, 10], [0.0, 0.1]]
    }
    
    # 生成样本
    param_values = sobol.sample(problem, 1000)
    
    # 这里需要实际计算模型输出
    # outputs = [...]
    
    # 分析敏感性
    # Si = sobol_analyze.analyze(problem, outputs, print_to_console=False)
    
    return None  # 返回敏感性指标

# ==================== 5. 优化模块 ====================
def optimize_measurement_parameters():
    """
    优化测量参数（入射角、波长范围等）以最大化测量精度
    """
    prob = pulp.LpProblem('Measurement_Optimization', pulp.LpMaximize)
    
    # 决策变量
    theta_min = pulp.LpVariable('theta_min', lowBound=0, upBound=30)  # 最小入射角
    theta_max = pulp.LpVariable('theta_max', lowBound=30, upBound=60)  # 最大入射角
    lambda_min = pulp.LpVariable('lambda_min', lowBound=400, upBound=800)  # 最小波长(nm)
    lambda_max = pulp.LpVariable('lambda_max', lowBound=1000, upBound=2000)  # 最大波长(nm)
    
    # 目标函数：最大化干涉条纹对比度（简化模型）
    prob += (lambda_max - lambda_min) * (theta_max - theta_min)
    
    # 约束条件
    prob += theta_max >= theta_min + 10  # 入射角范围至少10度
    prob += lambda_max >= lambda_min + 500  # 波长范围至少500nm
    
    # 求解
    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    
    return {
        'theta_range': [theta_min.value(), theta_max.value()],
        'wavelength_range': [lambda_min.value(), lambda_max.value()]
    }

# ==================== 6. 主流程 ====================
def main():
    """
    主函数：完整的碳化硅外延层厚度确定流程
    """
    # 1. 加载数据
    wavelengths, raw_intensities = load_interference_data('interference_data.csv')
    
    # 2. 预处理
    processed_intensities = preprocess_interference_signal(raw_intensities)
    
    # 3. 特征提取
    features = extract_interference_features(wavelengths, processed_intensities)
    print(f"检测到{features['peak_count']}个干涉峰")
    
    # 4. 物理模型反演
    thickness_physical, error = thickness_from_interference(
        wavelengths, processed_intensities,
        n_epi=2.6, n_sub=2.7, initial_guess=1000
    )
    print(f"物理模型反演厚度: {thickness_physical:.2f} nm, 拟合误差: {error:.4f}")
    
    # 5. 可视化
    plt.figure(figsize=(12, 8))
    
    plt.subplot(2, 2, 1)
    plt.plot(wavelengths, raw_intensities, 'b-', alpha=0.5, label='原始数据')
    plt.plot(wavelengths, processed_intensities, 'r-', label='处理后')
    plt.xlabel('波长 (nm)')
    plt.ylabel('反射强度')
    plt.title('红外干涉条纹')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    plt.subplot(2, 2, 2)
    model_intensity = interference_model(thickness_physical, wavelengths, 2.6, 2.7)
    plt.plot(wavelengths, processed_intensities, 'b-', label='实验数据')
    plt.plot(wavelengths, model_intensity, 'r--', label='拟合模型')
    plt.xlabel('波长 (nm)')
    plt.ylabel('反射强度')
    plt.title(f'模型拟合结果 (厚度={thickness_physical:.1f}nm)')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    plt.subplot(2, 2, 3)
    # 显示峰值检测结果
    peaks_idx = np.where(np.isin(wavelengths, features['peak_positions']))[0]
    plt.plot(wavelengths, processed_intensities, 'b-')
    plt.plot(wavelengths[peaks_idx], processed_intensities[peaks_idx], 'ro', label='峰值')
    plt.xlabel('波长 (nm)')
    plt.ylabel('反射强度')
    plt.title('干涉峰检测')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    plt.subplot(2, 2, 4)
    # 厚度误差分析
    thickness_range = np.linspace(thickness_physical*0.8, thickness_physical*1.2, 50)
    errors = [np.sum((interference_model(t, wavelengths, 2.6, 2.7) - processed_intensities)**2) 
              for t in thickness_range]
    plt.plot(thickness_range, errors, 'b-')
    plt.axvline(thickness_physical, color='r', linestyle='--', label=f'最优厚度={thickness_physical:.1f}nm')
    plt.xlabel('厚度 (nm)')
    plt.ylabel('拟合误差')
    plt.title('厚度-误差曲线')
    plt.legend()
    plt.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig('sic_epitaxy_analysis.png', dpi=300, bbox_inches='tight')
    plt.show()
    
    # 6. 优化测量参数
    optimal_params = optimize_measurement_parameters()
    print(f"\n优化后的测量参数:")
    print(f"  入射角范围: {optimal_params['theta_range'][0]:.1f} - {optimal_params['theta_range'][1]:.1f} 度")
    print(f"  波长范围: {optimal_params['wavelength_range'][0]:.0f} - {optimal_params['wavelength_range'][1]:.0f} nm")
    
    return {
        'thickness': thickness_physical,
        'fitting_error': error,
        'features': features,
        'optimal_parameters': optimal_params
    }

if __name__ == "__main__":
    # 生成示例数据（实际使用时从文件加载）
    np.random.seed(42)
    example_wavelengths = np.linspace(400, 2000, 1000)
    example_thickness = 1500  # nm
    example_intensity = interference_model(example_thickness, example_wavelengths, 2.6, 2.7)
    example_intensity += np.random.normal(0, 0.02, len(example_wavelengths))  # 添加噪声
    
    # 保存示例数据
    example_data = pd.DataFrame({
        'wavelength': example_wavelengths,
        'intensity': example_intensity
    })
    example_data.to_csv('interference_data.csv', index=False)
    
    # 运行主程序
    results = main()