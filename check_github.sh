#!/bin/bash
echo "GitHub状态检查脚本"
echo "=================="
echo "检查时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

cd /root/.openclaw/workspace/InfoData

echo "1. 📊 本地Git状态"
echo "   当前分支: $(git branch --show-current)"
echo "   最新提交: $(git log --oneline -1)"
echo "   提交哈希: $(git log --oneline -1 | cut -d' ' -f1)"
echo ""

echo "2. 📁 本地文件验证"
echo "   src/目录是否存在: $(if [ -d src/ ]; then echo "✅ 存在"; else echo "❌ 不存在"; fi)"
echo "   src/目录文件数: $(find src/ -type f 2>/dev/null | wc -l)"
echo "   关键文件检查:"
for file in "src/data_collection/base.py" "src/data_storage/manager.py" "daily_update_stock_info_new.py"; do
    if [ -f "$file" ]; then
        echo "     ✅ $file"
    else
        echo "     ❌ $file (缺失)"
    fi
done
echo ""

echo "3. 🔍 Git跟踪状态"
echo "   src/目录是否被Git跟踪:"
if git ls-files src/ >/dev/null 2>&1; then
    echo "     ✅ 是 (文件数: $(git ls-files src/ | wc -l))"
    git ls-files src/ | head -5 | sed 's/^/       /'
    if [ $(git ls-files src/ | wc -l) -gt 5 ]; then echo "       ..."; fi
else
    echo "     ❌ 否"
fi
echo ""

echo "4. 🌐 GitHub访问链接"
echo "   直接访问链接:"
echo "   - Main分支: https://github.com/HongtaiChen/InfoData/tree/main"
echo "   - Dev分支:  https://github.com/HongtaiChen/InfoData/tree/dev"
echo "   - 最新提交: https://github.com/HongtaiChen/InfoData/commit/$(git log --oneline -1 | cut -d' ' -f1)"
echo "   - 文件列表: https://github.com/HongtaiChen/InfoData"
echo ""

echo "5. 🛠️ 如果仍然看不到src/目录"
echo "   可能的解决方案:"
echo "   1. 等待GitHub缓存刷新 (1-5分钟)"
echo "   2. 按 Ctrl+F5 强制刷新浏览器"
echo "   3. 清除浏览器缓存"
echo "   4. 使用无痕/隐私模式访问"
echo "   5. 直接访问提交链接查看"
echo ""

echo "6. 📋 验证命令"
echo "   在终端中运行:"
echo "   curl -s https://api.github.com/repos/HongtaiChen/InfoData/git/trees/main | grep -o '\"path\":\"[^\"]*' | grep src"
echo ""

echo "检查完成!"
