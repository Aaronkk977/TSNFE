import argparse
import pandas as pd
import numpy as np
import os
import sys
import torch
import torch.nn as nn
from sklearn.metrics import mean_squared_error, mean_absolute_error
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor
from torch.utils.data import DataLoader, TensorDataset

class SimpleLSTM(nn.Module):
    def __init__(self, input_dim, hidden_dim=32, num_layers=1):
        super(SimpleLSTM, self).__init__()
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.linear = nn.Linear(hidden_dim, 1)

    def forward(self, x):
        # x shape: (batch, seq_len, features)
        out, _ = self.lstm(x)
        # We only want the last time step output
        out = self.linear(out[:, -1, :])
        return out

def evaluate(y_true, y_pred, model_name):
    mse = np.mean((y_true - y_pred)**2)
    mae = np.mean(np.abs(y_true - y_pred))
    
    # Directional Accuracy
    actual_dir = np.sign(y_true)
    pred_dir = np.sign(y_pred)
    # Ignore zero returns for direction accuracy
    valid_mask = (actual_dir != 0)
    
    if valid_mask.sum() > 0:
        dir_acc = np.mean(actual_dir[valid_mask] == pred_dir[valid_mask])
    else:
        dir_acc = 0.0

    print(f"--- {model_name} ---")
    print(f"MSE: {mse:.6f}")
    print(f"MAE: {mae:.6f}")
    print(f"Directional Accuracy: {dir_acc * 100:.2f}%")
    print()

def create_sequences(df, features, target, window_size=3):
    """
    Transforms dataframe into sequences per stock.
    If a stock doesn't have continuous `window_size` days, we pad or just skip.
    For simplicity, if we don't have enough data, we use window_size=1
    meaning we treat each day independently but with shape (batch, 1, features).
    """
    xs, ys = [], []
    for stock in df['stock_code'].unique():
        sdf = df[df['stock_code'] == stock].sort_values('date')
        if len(sdf) < window_size:
            continue
        
        x_data = sdf[features].values
        y_data = sdf[target].values
        
        for i in range(len(sdf) - window_size + 1):
            xs.append(x_data[i:i+window_size])
            ys.append(y_data[i+window_size-1])
            
    return np.array(xs), np.array(ys)

def train_lstm(X_train, y_train, X_test, y_test, input_dim):
    # Convert to PyTorch tensors
    X_train_t = torch.tensor(X_train, dtype=torch.float32)
    y_train_t = torch.tensor(y_train, dtype=torch.float32).view(-1, 1)
    X_test_t = torch.tensor(X_test, dtype=torch.float32)
    
    train_dataset = TensorDataset(X_train_t, y_train_t)
    train_loader = DataLoader(train_dataset, batch_size=16, shuffle=True)
    
    model = SimpleLSTM(input_dim)
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005)
    
    epochs = 50
    model.train()
    for __ in range(epochs):
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad()
            outputs = model(batch_x)
            loss = criterion(outputs, batch_y)
            loss.backward()
            optimizer.step()
            
    model.eval()
    with torch.no_grad():
        y_pred = model(X_test_t).numpy()
        
    return y_pred.flatten()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=5)
    args = parser.parse_args()
    
    data_file = f"data/ml_dataset_{args.days}d.csv"
    if not os.path.exists(data_file):
        print(f"Dataset {data_file} not found. Please run build_dataset.py first.")
        return
        
    df = pd.read_csv(data_file)
    
    target_col = f'return_{args.days}d'
    df = df.dropna(subset=[target_col])
    
    if len(df) == 0:
        print("No valid target data available after removing nulls.")
        return
        
    features = [
        'buy_count', 'sell_count', 
        'is_top_recommended', 'is_top_not_recommended',
        'recommended_view_sum', 'not_recommended_view_sum'
    ]
    
    X = df[features].values
    y = df[target_col].values
    
    # Train test split (chronological or random)
    # Simple random split for now
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Scale features
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    print(f"Dataset Size: {len(df)}")
    print("Features:", features)
    print()
    
    # 1. Linear Regression
    lr = LinearRegression()
    lr.fit(X_train_scaled, y_train)
    y_pred_lr = lr.predict(X_test_scaled)
    evaluate(y_test, y_pred_lr, "Linear Regression")
    
    # 2. XGBoost
    xgb = XGBRegressor(n_estimators=100, learning_rate=0.05, max_depth=3, random_state=42)
    xgb.fit(X_train_scaled, y_train)
    y_pred_xgb = xgb.predict(X_test_scaled)
    evaluate(y_test, y_pred_xgb, "XGBoost")
    
    # 3. LSTM
    # For LSTM, we need temporal structure. We use the full df and create sequences of length 2
    # If the dataset is too small, we just use seq=1 
    window_size = 2
    X_seq, y_seq = create_sequences(df, features, target_col, window_size=window_size)
    if len(X_seq) > 10:
        # We need to scale sequences properly, we'll scale the flat data and reshape
        # But for simplicity, we scale per feature
        seq_shape = X_seq.shape
        X_seq_flat = X_seq.reshape(-1, seq_shape[-1])
        X_seq_flat_scaled = scaler.transform(X_seq_flat)
        X_seq_scaled = X_seq_flat_scaled.reshape(seq_shape)
        
        # Split sequential data
        X_tr_seq, X_te_seq, y_tr_seq, y_te_seq = train_test_split(X_seq_scaled, y_seq, test_size=0.2, random_state=42)
        
        y_pred_lstm = train_lstm(X_tr_seq, y_tr_seq, X_te_seq, y_te_seq, input_dim=len(features))
        evaluate(y_te_seq, y_pred_lstm, "LSTM (RNN)")
    else:
        print("Not enough consecutive data for LSTM sequences. Treating window_size=1.")
        # treating window_size=1
        X_train_seq = X_train_scaled.reshape(X_train_scaled.shape[0], 1, X_train_scaled.shape[1])
        X_test_seq = X_test_scaled.reshape(X_test_scaled.shape[0], 1, X_test_scaled.shape[1])
        y_pred_lstm = train_lstm(X_train_seq, y_train, X_test_seq, y_test, input_dim=len(features))
        evaluate(y_test, y_pred_lstm, "LSTM (RNN, seq=1)")
        
if __name__ == "__main__":
    main()
