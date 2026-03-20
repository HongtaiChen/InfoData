#!/bin/bash
echo "InfoData项目GitHub推送最终验证"
echo "================================"
echo "验证时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

cd /root/.openclaw/workspace/InfoData

echo "1. 📊 本地Git状态"
echo "   当前分支: $(git branch --show-current)"
echo "   最新提交: $(git log --oneline -1)"
echo "   提交时间: $(git log -1 --format='%cd')"
echo "   未提交更改: $(git status --porcelain | wc -l)个文件"
echo ""

echo "2. 🔗 远程仓库状态"
git remote -v
echo ""
echo "   远程分支:"
git ls-remote --heads origin 2>/dev/null | head -5 || echo "   无法获取远程分支"
echo ""

echo "3. 📁 关键文件验证"
files=("src/data_collection/base.py" "src/data_storage/manager.py" "daily_update_stock_info_new.py" "insert_all_data_new.py" "MIGRATION_SUMMARY.md")
for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        if git ls-tree --name-only HEAD "$file" >/dev/null 2>&1; then
            echo "   ✅ $file (存在且已提交)"
        else
            echo "   ⚠️  $file (存在但未提交)"
        fi
    else
        echo "   ❌ $file (不存在)"
    fi
done
echo ""

echo "4. 🌐 GitHub访问信息"
echo "   主分支: https://github.com/HongtaiChen/InfoData/tree/main"
echo "   开发分支: https://github.com/HongtaiChen/InfoData/tree/dev"
echo "   提交记录: https://github.com/HongtaiChen/InfoData/commits/main"
echo "   最新提交: https://github.com/HongtaiChen/InfoData/commit/$(git log --oneline -1 | cut -d' ' -f1)"
echo ""

echo "5. 🚀 推送状态总结"
if git log --oneline origin/main 2>/dev/null | head -1 | grep -q "$(git log --oneline -1 | cut -d' ' -f1)"; then
    echo "   ✅ main分支推送成功"
else
    echo "   ⚠️  main分支可能需要推送"
fi

if git log --oneline origin/dev 2>/dev/null | head -1 | grep -q "$(git log --oneline -1 | cut -d' ' -f1)"; then
    echo "   ✅ dev分支推送成功"
else
    echo "   ⚠️  dev分支可能需要推送"
fi
echo ""

echo "6. 📋 建议操作"
echo "   a. 等待1-2分钟让GitHub刷新"
echo "   b. 访问: https://github.com/HongtaiChen/InfoData"
echo "   c. 按 Ctrl+F5 强制刷新浏览器"
echo "   d. 检查src/目录是否存在"
echo ""

echo "验证完成!"
