import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-darkgrid')

import torch
import torch.nn as nn
import torch.nn.functional as F

from sklearn.preprocessing import MinMaxScaler
from models.ANN import ANN
from tqdm.auto import trange
from sklearn.metrics import r2_score

"""
TODO-List
ANN - Single, Multi channel(O)
LSTM - Stateful
------
, Stateless ()
Transformer - PatchTST
------

"""
class TimeSeriesDataset(torch.utils.data.Dataset):
    '''
    TODO(영준)
    멀티 채널이 입력으로 들어갈 떄, y가 목표 컬럼만 나올 수 있도록
    '''
    def __init__(self, ts:np.array, lookback_size:int, forecast_size:int, target_column:int = None):
        self.lookback_size = lookback_size
        self.forecast_size = forecast_size
        self.data = ts
        self.target_column = target_column

    def __len__(self):
        return len(self.data) - self.lookback_size - self.forecast_size + 1

    def __getitem__(self, i):
        idx = (i+self.lookback_size)
        look_back = self.data[i:idx]
        forecast = self.data[idx:idx+self.forecast_size]
        
        '''
        Data shape
        single-channel: (len_dataset, 1)
        multi-channel: (len_dataset, c_in)
        '''
        if self.data.shape[1] != 1: # 컬럼 수가 1개가 아니라면
            if not self.target_column: # 타겟 컬럼 설정이 안되어있다면
                raise NotImplementedError("multi-columns 입력은 타겟 컬럼이 설정되어야 합니다.")
            forecast = forecast[:,self.target_column] # (32,22) -> (32)
        return look_back, forecast.squeeze() # squeeze : (32, 1) -> (32)

def mape(y_pred, y_true):
    return (np.abs(y_pred - y_true)/y_true).mean() * 100

def mae(y_pred, y_true):
    return np.abs(y_pred - y_true).mean()


def main(cfg):
    ################ 1. Dataset Load  ################
    dataset_setting = cfg.get("dataset_setting")
    use_single_channel = cfg.get("use_single_channel")
    main_csv = dataset_setting.get("main_csv")
    time_axis = dataset_setting.get("time_axis")
    target = dataset_setting.get("target")
    
    data = pd.read_csv(main_csv)
    if use_single_channel:
        data = pd.DataFrame(data.loc[:, [target, time_axis]])
    # data_only_pm25 = pd.DataFrame(data.loc[:, ["PM-2.5", "일시"]])
    data[time_axis] = pd.to_datetime(data[time_axis])
    data.index = data[time_axis]
    del data[time_axis]
    
    m_data = data.copy()
    m_data = m_data.dropna()
    target_column = data.columns.get_loc(target)
    ##################################################
    
    ############### 2. Preprocessing  ################
    # hyperparameter
    window_params = cfg.get("window_params")
    tst_size = window_params.get("tst_size")
    lookback_size = window_params.get("lookback_size")
    forecast_size = window_params.get("forecast_size")
    
    train_params = cfg.get("train_params")
    epochs = train_params.get("epochs")
    data_loader_params = train_params.get("data_loader_params")

    # 결측치 처리는 완전히 되었다고 가정
    
    # scaling
    scaler = MinMaxScaler()
    trn_scaled = scaler.fit_transform(m_data[:-tst_size].to_numpy(dtype=np.float32))
    tst_scaled = scaler.transform(m_data[-tst_size-lookback_size:].to_numpy(dtype=np.float32))

    trn_ds = TimeSeriesDataset(trn_scaled, lookback_size, forecast_size, target_column=target_column)
    tst_ds = TimeSeriesDataset(tst_scaled, lookback_size, forecast_size, target_column=target_column)

    trn_dl = torch.utils.data.DataLoader(trn_ds, **data_loader_params)
    tst_dl = torch.utils.data.DataLoader(tst_ds, batch_size=tst_size, shuffle=False)
    ##################################################
    
    ########## 3. Train Hyperparams setting ##########
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    # hyperparameter
    if use_single_channel:
        c_in = 1
    else:
        x, _ = next(iter(trn_dl))
        c_in = x.shape[2]
        
        
    # model stting
    model = cfg.get("model")
    model_params = cfg.get("ann_model_params")
    model_params["d_in"] = lookback_size
    model_params["d_out"] = forecast_size
    model_params["c_in"] = c_in
    model = model(**model_params)
    model.to(device)
    
    # optimzer / loss setting
    Optim = train_params.get('optim')
    optim_params = train_params.get('optim_params')
    optim = Optim(model.parameters(), **optim_params)
    
    loss_func = train_params.get("loss")
    
    pbar = trange(epochs)
    ##################################################
    
    ################### 4. Train #####################
    for i in pbar:
        model.train()
        trn_loss = .0
        for x, y in trn_dl:
            x, y = x.flatten(1).to(device), y.to(device)   # (32, 18), (32, 4)
            p = model(x)
            optim.zero_grad()
            loss = loss_func(p, y)
            loss.backward()
            optim.step()
            trn_loss += loss.item()*len(y)
        trn_loss = trn_loss/len(trn_ds)

        model.eval()
        with torch.inference_mode():
            x, y = next(iter(tst_dl))
            x, y = x.flatten(1).to(device), y.to(device)
            p = model(x)
            tst_loss = loss_func(p,y)
        pbar.set_postfix({'loss':trn_loss, 'tst_loss':tst_loss.item()})
    ##################################################
    
    ################# 5. Evaluation ##################
    model.eval()
    with torch.inference_mode():
        x, y = next(iter(tst_dl))
        x, y = x.flatten(1).to(device), y.to(device)
        p = model(x)

        y = y.cpu()/scaler.scale_[0] + scaler.min_[0]
        p = p.cpu()/scaler.scale_[0] + scaler.min_[0]

        y = np.concatenate([y[:,0], y[-1,1:]])
        p = np.concatenate([p[:,0], p[-1,1:]])
    ##################################################
    
    #################### 6. Plot #####################
    plt.title(f"Neural Network, MAPE:{mape(p,y):.4f}, MAE:{mae(p,y):.4f}, R2:{r2_score(p,y):.4f}")
    plt.plot(range(tst_size), y, label="True")
    plt.plot(range(tst_size), p, label="Prediction")
    plt.legend()
    plt.savefig("figs/graph.jpg", format="jpeg")
    ##################################################
    

def get_args_parser(add_help=True):
  import argparse
  
  parser = argparse.ArgumentParser(description="Pytorch K-fold Cross Validation", add_help=add_help)
  parser.add_argument("-c", "--config", default="./config.py", type=str, help="configuration file")

  return parser

if __name__ == "__main__":
  args = get_args_parser().parse_args()
  exec(open(args.config).read())
  
  main(config)