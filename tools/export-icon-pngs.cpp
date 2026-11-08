#include "PakFile.hpp"      // GameLauncher/src（編譯時 -I 指向它，見 README.md）
#include "SpriteSheet.hpp"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wincodec.h>

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <set>
#include <string>
#include <vector>

namespace fs = std::filesystem;

template <class T>
void release(T*& ptr) {
    if (ptr) {
        ptr->Release();
        ptr = nullptr;
    }
}

bool savePng(const Surface& image, const fs::path& path, std::string& err) {
    if (image.width <= 0 || image.height <= 0 || image.pixels.empty()) {
        err = "空圖";
        return false;
    }
    IWICImagingFactory* factory = nullptr;
    IWICBitmap* bitmap = nullptr;
    IWICStream* stream = nullptr;
    IWICBitmapEncoder* encoder = nullptr;
    IWICBitmapFrameEncode* frame = nullptr;
    IPropertyBag2* props = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory));
    if (SUCCEEDED(hr)) {
        hr = factory->CreateBitmapFromMemory(static_cast<UINT>(image.width), static_cast<UINT>(image.height),
                                             GUID_WICPixelFormat32bppBGRA, static_cast<UINT>(image.width * 4),
                                             static_cast<UINT>(image.pixels.size() * 4),
                                             reinterpret_cast<BYTE*>(const_cast<uint32_t*>(image.pixels.data())), &bitmap);
    }
    if (SUCCEEDED(hr)) hr = factory->CreateStream(&stream);
    if (SUCCEEDED(hr)) hr = stream->InitializeFromFilename(path.wstring().c_str(), GENERIC_WRITE);
    if (SUCCEEDED(hr)) hr = factory->CreateEncoder(GUID_ContainerFormatPng, nullptr, &encoder);
    if (SUCCEEDED(hr)) hr = encoder->Initialize(stream, WICBitmapEncoderNoCache);
    if (SUCCEEDED(hr)) hr = encoder->CreateNewFrame(&frame, &props);
    if (SUCCEEDED(hr)) hr = frame->Initialize(props);
    if (SUCCEEDED(hr)) hr = frame->SetSize(static_cast<UINT>(image.width), static_cast<UINT>(image.height));
    WICPixelFormatGUID format = GUID_WICPixelFormat32bppBGRA;
    if (SUCCEEDED(hr)) hr = frame->SetPixelFormat(&format);
    if (SUCCEEDED(hr)) hr = frame->WriteSource(bitmap, nullptr);
    if (SUCCEEDED(hr)) hr = frame->Commit();
    if (SUCCEEDED(hr)) hr = encoder->Commit();
    release(props);
    release(frame);
    release(encoder);
    release(stream);
    release(bitmap);
    release(factory);
    if (FAILED(hr)) {
        err = "PNG 寫出失敗";
        return false;
    }
    return true;
}

int wmain(int argc, wchar_t** argv) {
    if (argc < 5) {
        std::wcerr << L"用法: export-icon-pngs <clientRoot> <ids.txt> <outDir> <prefix>\n";
        return 2;
    }
    const fs::path clientRoot = argv[1];
    const fs::path idsPath = argv[2];
    const fs::path outDir = argv[3];
    const std::wstring prefixW = argv[4];
    std::string prefix(prefixW.begin(), prefixW.end());

    std::set<int> ids;
    std::ifstream idsIn(idsPath);
    int id = 0;
    while (idsIn >> id) {
        if (id > 0) ids.insert(id);
    }

    std::vector<std::unique_ptr<PakFile>> paks;
    for (int i = -1; i < 16; ++i) {
        const std::wstring stem = i < 0 ? L"Sprite" : (i < 10 ? L"Sprite0" : L"Sprite") + std::to_wstring(i);
        auto pak = std::make_unique<PakFile>();
        std::string err;
        if (pak->open(clientRoot / (stem + L".idx"), clientRoot / (stem + L".pak"), err)) {
            paks.push_back(std::move(pak));
        }
    }
    fs::create_directories(outDir);
    const HRESULT co = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    int exported = 0;
    int missing = 0;
    for (int iconId : ids) {
        const fs::path out = outDir / (prefix + std::to_string(iconId) + ".png");
        if (fs::exists(out)) continue;
        std::vector<uint8_t> bytes;
        const std::string tbt = std::to_string(iconId) + ".tbt";
        for (const auto& pak : paks) {
            if (pak->read(tbt, bytes)) break;
        }
        if (bytes.empty()) {
            ++missing;
            continue;
        }
        Surface icon;
        if (!decodeIcon(bytes, icon)) {
            ++missing;
            continue;
        }
        std::string err;
        if (savePng(icon, out, err)) {
            ++exported;
        } else {
            ++missing;
        }
    }
    if (SUCCEEDED(co)) CoUninitialize();
    std::cout << "ids=" << ids.size() << " exported=" << exported << " missing=" << missing << "\n";
    return 0;
}
