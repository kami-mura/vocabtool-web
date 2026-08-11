# 第三方声明

本仓库包含或使用了以下第三方开源内容，特此声明与致谢。

## 一、直接进入仓库的内容

### JSZip

- 文件：`app/static/vendor/jszip.min.js`
- 版本：3.10.1
- 版权：Copyright (c) 2009-2016 Stuart Knightley
- 许可：MIT 或 GPLv3（双许可，本项目按 MIT 使用）
- 项目主页：<http://stuartk.com/jszip>
- 内部依赖 pako 为 MIT 许可。

### fflate

- 文件：`app/static/vendor/fflate.min.js`
- 版权：Copyright (c) 101arrowz（Arjun Barrett）
- 许可：MIT
- 项目主页：<https://github.com/101arrowz/fflate>

### NGSL 词频表

- 文件：`data/ngsl_sfi_31k.csv`
- 数据来源：New General Service List（NGSL），作者 Charles Browne、Brent Culligan、Joseph Phillips，发布在 <https://www.newgeneralservicelist.com>
- 许可：Creative Commons Attribution-ShareAlike 4.0 International（CC BY-SA 4.0），全文见 <https://creativecommons.org/licenses/by-sa/4.0/legalcode>
- 说明：本文件是 NGSL 合并 SFI 词频的 31k 衍生词表。CC BY-SA 仅约束该数据本身的再分发（再分发须署名并保持相同许可），不约束本应用的其他代码。若该 CSV 来自第三方整理的衍生表，请补充该整理者的署名与许可链接。

## 二、运行时依赖（pip 安装，未随源码分发）

| 包 | 许可证 |
|---|---|
| fastapi | MIT |
| sqlalchemy | MIT |
| uvicorn | BSD-3-Clause |
| jinja2 | BSD |
| httpx | BSD-3-Clause |
| python-dotenv | BSD-3-Clause |
| pypdf | BSD-3-Clause |
| python-docx | MIT |
| openpyxl | MIT |
| xlrd | BSD |
| openai | Apache-2.0 |
| fsrs（py-fsrs，FSRS-6 调度） | MIT |
| pwdlib | MIT |
| pytest | MIT |
| chardet | LGPL |
| psycopg2-binary | LGPL with exceptions |
| edge-tts | LGPLv3 |

## MIT License 全文

以下 MIT 许可适用于上面列出的所有 MIT 许可项目：

```text
MIT License

Copyright (c) <各项目版权人，见上文>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 说明

本文件用于满足 MIT/CC BY-SA 许可的署名与许可声明要求，并整理运行时依赖的许可证信息，不构成法律意见。

## 内置词表

应用当前注册 43 个内置词表。词表文件位于 `data/wordlists/`；NGSL 1.2 核心词表直接取自 `data/ngsl_sfi_31k.csv` 的前 2,809 项。最早加入的 9 个词表来源如下（未经完整版权核实）：

| id | 名称 | 来源（一行简述） |
|---|---|---|
| primary | 小学英语（人教版教材 + 小学大纲） | 人教版小学英语教材词汇（网易有道词典词书，经 kajweb/dict 收录）+ mahavivo/english-wordlists《小学英语大纲词汇》并集 |
| junior | 初中英语（中考大纲） | mahavivo/english-wordlists《中考英语词汇表》 |
| senior | 高中英语（高考大纲） | 网易有道词典《高中英语词汇（正序版）》，依据教育部高考英语大纲及高中英语课程标准（kajweb/dict） |
| cet4 | 大学英语四级 | mahavivo/english-wordlists《大学英语四级大纲单词表》（共 4615 词，源自《全国大学英语四、六级考试大纲》相关公开整理） |
| cet6 | 大学英语六级 | 四、六级大纲词汇并集：mahavivo/english-wordlists CET4_edited + CET6_edited（源自《全国大学英语四、六级考试大纲》公开整理） |
| kaoyan | 考研英语 | mahavivo/english-wordlists NPEE_Wordlist.txt（全国硕士研究生入学考试大纲词汇表公开整理） |
| ielts | 雅思 | 新东方《雅思词汇词根+联想记忆法》+ 网易有道词典《雅思词汇》并集（kajweb/dict） |
| toefl | 托福 | mahavivo/english-wordlists TOEFL.txt（源自 2003 年版金山词霸词库整理） |
| gre | GRE | 新东方《GRE 词汇精选》（红宝书）词条（kajweb/dict） |

此前加入的另外 18 个词表如下；这些文件来自公开整理或公开词典列表，仍需在正式公开分发或商业使用前逐项复核其再分发许可：

| id | 名称 | 登记来源 |
|---|---|---|
| tem4 / tem8 | 英语专业四级 / 八级 | 专四、专八大纲词汇公开整理 |
| sat / act | SAT / ACT | 公开高频词表近似 |
| awl | 学术词汇 AWL | Coxhead Academic Word List 570 |
| coca5k | COCA 高频 5000 | COCA 语料库公开频率列表 |
| mba / zhicheng | MBA 联考 / 职称英语 | 对应考试词汇公开整理 |
| oxford3000 / oxford5000 | 牛津 3000 / 5000 | Oxford Learner's Word Lists |
| longman3000 / longman9000 | 朗文 3000 / 9000 | Longman Communication 3000/9000 |
| collins1–collins5 | 柯林斯 1–5 星 | Collins COBUILD 词频公开整理 |
| vocabcom1000 | Vocabulary.com Top 1000 | Vocabulary.com 公布列表 |

### 2026-08-10 新增词表

以下 15 个入口来自可追溯的公开资料。词表文件只保留词或短语本身，不复制释义、例句或正文：

| id | 名称 | 来源与许可 |
|---|---|---|
| ngsl_core | NGSL 1.2 核心 2809 | New General Service List 1.2，Browne、Culligan 与 Phillips，CC BY-SA 4.0，<https://www.newgeneralservicelist.com/new-general-service-list> |
| ngsl_spoken | 日常口语 NGSL-S | NGSL-Spoken 1.2，Browne 与 Culligan，CC BY-SA 4.0，<https://www.newgeneralservicelist.com/ngsl-spoken> |
| nawl | 新学术词汇 NAWL | New Academic Word List 1.2，Browne、Culligan 与 Phillips，CC BY-SA 4.0，<https://www.newgeneralservicelist.com/new-academic-word-list> |
| bsl | 商务英语 BSL | Business Service List 1.2，Browne 与 Culligan，CC BY-SA 4.0，<https://www.newgeneralservicelist.com/business-service-list> |
| toeic | TOEIC TSL | TOEIC Service List 1.2，Browne 与 Culligan，CC BY-SA 4.0，<https://www.newgeneralservicelist.com/toeic-service-list> |
| medical | 医学口语 MOEL | Medical Oral English List 1.0，Chartrand 与 Dilenschneider，CC BY-SA 4.0，<https://www.newgeneralservicelist.com/medical-oral-english-list> |
| phave150 | 高频短语动词 PHaVE 150 | Garnier 与 Schmitt (2015) PHaVE List 的公开附录；Nottingham 仓储许可允许个人研究、学习、教育和非营利用途，其他用途应另行取得许可，<https://globalaffairs.ucdavis.edu/iae/graduate/language-tips/high-frequency-phrasal-verbs> |
| academic_collocations | 学术搭配 ACL | Pearson Academic Collocation List 2025 公开 PDF；PDF 编号跳过 2087，因此源文件实际可解析 2468 条；未发现单独的开放再分发许可，正式公开分发或商业使用前应向 Pearson 确认，<https://www.pearsonpte.com.cn/content/dam/ELL/pte/pearsonpte/pdfs/the-academic-collocation-list.pdf> |
| programming | IT 与编程术语 | MDN Glossary 条目标题，MDN prose content 为 CC BY-SA 2.5，<https://github.com/mdn/content> |
| finance | 金融与投资术语 | 美国证券交易委员会 Investor.gov Glossary 的英文条目标题，<https://www.investor.gov/introduction-investing/investing-basics/glossary/all> |
| cefr_a1 / cefr_a2 / cefr_b1 / cefr_b2 | CEFR-J A1–B2 | CEFR-J Vocabulary Profile 1.5；允许免费研究和商业使用，但须署名 Tono Laboratory, TUFS，<https://github.com/openlanguageprofiles/olp-en-cefrj> |
| cefr_c1 / cefr_c2 | CEFR-J C1–C2 | Octanove Vocabulary Profile C1/C2 1.0，CC BY-SA 4.0，<https://github.com/openlanguageprofiles/olp-en-cefrj> |

CC BY-SA 数据的署名与相同方式共享要求适用于对应词表数据及其衍生版本，不改变本应用其他独立代码的许可证。
