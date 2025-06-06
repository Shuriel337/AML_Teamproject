# main.py

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader, Subset
from torchvision import transforms
import torchvision.transforms.functional as TF
import os
import shutil
from tqdm import tqdm
import matplotlib.pyplot as plt
from PIL import Image

from model import Generator, Discriminator
from pytorch_fid import fid_score   ###FID ?��문에 ?���? ?��치한 �?


class FFHQDataset(Dataset):
    def __init__(self, img_dir, transform=None):
        super().__init__()
        self.img_dir = img_dir
        self.image_files = [
            f for f in os.listdir(img_dir)
            if f.lower().endswith(".png")
        ]
        self.transform = transform

    def __len__(self):
        return len(self.image_files)

    def __getitem__(self, idx):
        path = os.path.join(self.img_dir, self.image_files[idx])
        img = Image.open(path).convert("RGB")
        if self.transform:
            img = self.transform(img)
        return img


def load_data(batch_size: int, img_dir: str, max_images: int = None) -> DataLoader:
    """
    FFHQ256 ?��?��?��?�� 로더 반환 (?�� max_images개만 ?��?��)
    Args:
        batch_size (int): 배치 ?���?
        img_dir (str): ?��미�?? ?��?��?�� ?��?�� 경로
        max_images (int, optional): ?��?��?�� 최�?? ?��미�?? ?�� (None?�� 경우 ?���?)
    """
    
    # FFHQ256 ?��미�?? ?��처리 ?��?��?��?��?��
    transform = transforms.Compose([
        transforms.Resize(256),
        transforms.ToTensor(),
        transforms.Normalize((0.5, 0.5, 0.5),
                             (0.5, 0.5, 0.5))
    ])
    full_dataset = FFHQDataset(img_dir, transform)

    if max_images is not None and max_images < len(full_dataset):
        indices = list(range(max_images))
        dataset = Subset(full_dataset, indices)
    else:
        dataset = full_dataset

    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=True,      
        num_workers=2,
        pin_memory=True,
        drop_last=True
    )


def train(
    generator: torch.nn.Module,
    discriminator: torch.nn.Module,
    dataloader: DataLoader,
    epochs: int,
    lr: float,
    device: torch.device,
    fid_batch_size: int = 32,
    fid_num_images: int = 1000,
    fid_every: int = 5
):
    """
    ?��?�� 루프 ?��?�� + 주기?��?���? FID 계산
    Args:
        generator (nn.Module): Generator 모델
        discriminator (nn.Module): Discriminator 모델
        dataloader (DataLoader): ?��?��?�� ?��?��?��로더 (Subset ?��?��, max_images ?��?��?��)
        epochs (int): ?��?�� ?��
        lr (float): ?��?���?
        device (torch.device): ?��?�� ?��바이?��
        ###fid_batch_size (int): FID 계산 ?�� ?�� 번에 ?��?��/?��?�� ?��미�?? �? 개씩 처리?���?
        ###fid_num_images (int): FID 계산?�� ?��?��?�� real/fake ?��미�?? 개수 (?��: 1000)
        ###fid_every (int): �? ?��?��마다 FID�? 계산?���? (?��: 5)
    """

    ### ?��?�� ?��?�� �? ?��?��마이??? ?��?��
    criterion = nn.BCEWithLogitsLoss()
    optimizer_G = optim.Adam(generator.parameters(),
                             lr=lr, betas=(0.5, 0.999))
    optimizer_D = optim.Adam(discriminator.parameters(),
                             lr=lr, betas=(0.5, 0.999))

    ### ?��?���? 차원 (Generator ?��?�� ?��기에 맞춰 조정)
    nz = generator.noise_dim
    real_dataset = dataloader.dataset
    num_real_images = len(real_dataset)   #max_images??? ?��?��(10000)


    # fid
    fid_real_indices = list(range(min(fid_num_images, num_real_images))) 
    fid_real_subset = Subset(real_dataset, fid_real_indices) 
    fixed_fid_noise = torch.randn(len(fid_real_indices), nz, device=device)  

    ### ?��?���? ?��?��
    real_label_val = 0.9 ###발산 ?��?�� ?��?�� 1->0.9�? ?��?��
    fake_label_val = 0.0

    # ?��?�� 기록
    G_losses, D_losses = [], []
    
    # 결과 ????�� ?��?��?���? ?��?��
    os.makedirs("./results", exist_ok=True)

    print("?��?��?�� ?��?��?��?��?��...")
    for epoch in range(epochs):
        epoch_D_loss, epoch_G_loss = 0.0, 0.0
        # 진행�? ?��?���?
        pbar = tqdm(dataloader, desc=f"Epoch {epoch+1}/{epochs}")

        # ?��?�� 루프
        for real_imgs in pbar:
            batch_size = real_imgs.size(0)
            real_imgs = real_imgs.to(device)

            # (1) Discriminator ?��?��?��?��
            discriminator.zero_grad()
            ### ?��?�� ?��미�???�� ????�� ?��?��
            labels_real = torch.full((batch_size,), real_label_val, device=device)
            out_real = discriminator(real_imgs).view(-1)
            errD_real = criterion(out_real, labels_real)
            errD_real.backward()
            ### sigmoid ?��?���? ?��?�� raw logit 값을 ?��률로 �??��
            D_x = torch.sigmoid(out_real).mean().item()

            # �?�? ?��미�?? ?��?��
            noise = torch.randn(batch_size, nz, device=device)
            fake_imgs = generator(noise)
            
            ### �?�? ?��미�???�� ????�� ?��?��
            ###기존 코드(label.fill)?�� in-place ?��?��?��?�� �??��?�� ?���? ?�� ?��?��?�� ?��?���? ?��?��
            labels_fake = torch.full((batch_size,), fake_label_val, device=device)
            out_fake = discriminator(fake_imgs.detach()).view(-1)
            errD_fake = criterion(out_fake, labels_fake)
            errD_fake.backward()
            D_G_z1 = torch.sigmoid(out_fake).mean().item()

            # Discriminator ?��?��미터 ?��?��?��?��
            optimizer_D.step()
            
            ### (2) Generator ?��?��?��?��
            generator.zero_grad()
            
            ###기존 코드(label.fill)?�� in-place ?��?��?��?�� �??��?�� ?���? ?�� ?��?��?�� ?��?���? ?��?��
            out_fake_for_G = discriminator(fake_imgs).view(-1)
            errG = criterion(out_fake_for_G, labels_real)
            errG.backward()
            optimizer_G.step()
            D_G_z2 = torch.sigmoid(out_fake_for_G).mean().item()
            
            # ?��?�� 기록
            epoch_D_loss += (errD_real + errD_fake).item()
            epoch_G_loss += errG.item()

            # 진행�? ?��?���? ?��?��?��?��
            pbar.set_postfix({
                "D_loss": f"{(errD_real+errD_fake).item():.4f}",
                "G_loss": f"{errG.item():.4f}",
                "D(x)": f"{D_x:.4f}",
                "D(G(z))": f"{D_G_z1:.4f}/{D_G_z2:.4f}"
            })

        ### ?�� ?��?�� ?��?�� ?�� ?��?�� ?�� GPU 메모�? ?���?
        torch.cuda.empty_cache()

        # ?��?���? ?���? ?��?�� 계산
        avg_D = epoch_D_loss / len(dataloader)
        avg_G = epoch_G_loss / len(dataloader)
        
        G_losses.append(avg_G)
        D_losses.append(avg_D)

        # �? ?��?��마다 ?��?�� ?��?�� ?�� "16개만 미리보기" ????�� (?��?��링�?? ?���?)
        with torch.no_grad():
            preview_noise = torch.randn(16, nz, device=device)
            preview_images = generator(preview_noise).cpu()  # (16,3,256,256)
        os.makedirs("./results/samples", exist_ok=True)
        plt.figure(figsize=(8, 8))
        plt.axis("off")
        for idx in range(16):
            img = preview_images[idx]
            img_01 = (img + 1) / 2.0
            plt.subplot(4, 4, idx + 1)
            plt.imshow(img_01.permute(1, 2, 0))
            plt.axis("off")
        plt.tight_layout()
        plt.savefig(f"./results/samples/gen_epoch_{epoch+1}.png")
        plt.close()

        # 모델 체크?��?��?�� ????��
        torch.save({
            "epoch": epoch + 1,
            "generator_state_dict": generator.state_dict(),
            "discriminator_state_dict": discriminator.state_dict(),
            'optimizer_G_state_dict': optimizer_G.state_dict(),
            'optimizer_D_state_dict': optimizer_D.state_dict(),
            'G_losses': G_losses,
            'D_losses': D_losses,
        }, f"./results/ckpt_epoch_{epoch+1}.pth")

        ### FID 계산 주기?���? ?��?�� 
        if (epoch + 1) % fid_every == 0:
            #FID ?��?�� 직전 GPU 메모�? ?���?
            torch.cuda.empty_cache()

            #?��?��?��?���? 결과 ?���? ?��?�� eval()�? �?�?
            generator.eval()
            discriminator.eval()

            #?��미�?? ?��?�� ?��?��
            fid_root = "./results/fid"
            real_dir = os.path.join(fid_root, "real")
            fake_dir = os.path.join(fid_root, "fake")

            if os.path.exists(fid_root):
                shutil.rmtree(fid_root)
            os.makedirs(real_dir)
            os.makedirs(fake_dir)

            # (1) Real ?��미�?? ????�� (fid_num_images?��)
            # shuffle=False ?���? ?��문에 epoch마다 ?���??�� ?���? FID 계산 �??��
            real_loader_for_fid = DataLoader(
                fid_real_subset,
                batch_size=fid_batch_size,
                shuffle=False,
                num_workers=2,
                pin_memory=True,
                drop_last=False
            )
            # ?��미�??(-1,1)�? (0,1)�? �??�� ?�� ????��
            real_idx = 0
            for real_batch in real_loader_for_fid:
                for img in real_batch:
                    img_01 = (img + 1) / 2.0
                    pil_img = TF.to_pil_image(img_01)
                    pil_img.save(os.path.join(real_dir, f"real_{real_idx:05d}.png"))
                    real_idx += 1

            # (2) Fake ?��미�?? ?��?�� ?�� ????��
            fake_idx = 0
            with torch.no_grad():
                for start in range(0, len(fid_real_indices), fid_batch_size):
                    end = min(start + fid_batch_size, len(fid_real_indices))
                    noise_batch = fixed_fid_noise[start:end]
                    #fake image mini batch�? 만든 ?�� PIL�? ????��?���? ?��?�� CPU�? ?��?��
                    fake_batch = generator(noise_batch).cpu()

                    # ?��미�??(-1,1)�? (0,1)�? �??�� ?�� ????��
                    for img in fake_batch:
                        img_01 = (img + 1) / 2.0
                        pil_img = TF.to_pil_image(img_01)
                        pil_img.save(os.path.join(fake_dir, f"fake_{fake_idx:05d}.png"))
                        fake_idx += 1

                    del fake_batch, noise_batch
                    torch.cuda.empty_cache()

            torch.cuda.empty_cache()

            #FID 계산
            paths = [real_dir, fake_dir]
            fid_value = fid_score.calculate_fid_given_paths(
                paths, batch_size=fid_batch_size, device=device, dims=2048
            )
            print(f"Epoch {epoch+1:03d} | FID: {fid_value:.4f}")

            # real/fake ?��미�?? ?��?��?��리는 FID 계산 ?�� ?��?��
            shutil.rmtree(real_dir)
            shutil.rmtree(fake_dir)

            #eval() 모드????�� 것을 ?��?�� train() 모드�? 복원
            generator.train()
            discriminator.train()

        print(f"Epoch [{epoch+1}/{epochs}] D_loss: {avg_D:.4f}, G_loss: {avg_G:.4f}")

    # ?��?�� ?���? ?�� ?��?�� 그래?�� ????��
    torch.cuda.empty_cache()  # ?��?�� 종료 ?��?��?�� 메모�? ?���?
    plt.figure(figsize=(6, 4))
    plt.plot(G_losses, label="G")
    plt.plot(D_losses, label="D")
    plt.xlabel("Epoch")
    plt.ylabel("Loss")
    plt.legend()
    plt.savefig("./results/training_loss.png")
    plt.close()

    print("?��?��?�� ?��료되?��?��?��?��!")


if __name__ == "__main__":
    # 배치 ?��기�?? ?��?��?�� ?��미�?? ?��(max_images) 조정
    batch_size = 16       # ?��?��?�� 배치 ?���? (?��: 4, 8, 16 ?��)
    max_images = 10000   # ?��?�� 10000개만 ?��?��
    epochs = 100         # ?���? ?��?�� ?��
    lr = 0.00005     # ?��?���?

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    img_dir = "/home/elicer/AML_Teamproject/ffhq256"

    # ?��?��?�� 로딩
    print("?��?��?�� 로딩 �?...")
    dataloader = load_data(batch_size, img_dir, max_images=max_images)
    print(f"?��?��?��?�� (?�� {max_images}�?) ?���?: {len(dataloader.dataset)}")

    # 모델 ?��?��
    print("모델 ?��?�� �?...")
    G = Generator().to(device)
    D = Discriminator().to(device)

    # 모델 ?��?��미터 ?�� 출력
    def count_params(m):
        return sum(p.numel() for p in m.parameters() if p.requires_grad)

    print(f"Generator ?��?��미터: {count_params(G):,}")
    print(f"Discriminator ?��?��미터: {count_params(D):,}")

    # ?��?�� ?��?��
    train(
        G, D, dataloader, epochs, lr, device,
        fid_batch_size=16,
        fid_num_images=1000,
        fid_every=1
    )
