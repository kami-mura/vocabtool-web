"""内置查词样本；验证效果后可批量扩展到完整 NGSL。"""

from __future__ import annotations

_ENTRIES = {
    "run": """run /rʌn/
1. 跑，奔跑 | Move quickly on foot
• She runs every morning before work.
她每天上班前跑步。

2. 经营，管理 | Operate or manage something
• They run a small bookstore near the station.
他们在车站附近经营一家小书店。

3. 运转，运行 | Function or operate
• The machine runs quietly at night.
这台机器夜间运行得很安静。""",
    "set": """set /set/
1. 放置，摆放 | Put something in a particular place
• She set the keys beside the door.
她把钥匙放在门边。

2. 设定，确定 | Decide or establish something
• We set a clear goal for this month.
我们为这个月设定了明确目标。""",
    "point": """point /pɔɪnt/
1. 要点，观点 | An important idea or detail
• You made a good point during the meeting.
你在会议上提出了一个很好的观点。

2. 指向 | Direct attention toward something
• He pointed to the name on the list.
他指向名单上的名字。""",
    "matter": """matter /ˈmætər/
1. 事情，问题 | A subject or situation
• We need to discuss an important matter.
我们需要讨论一件重要的事情。

2. 要紧，有关系 | Be important
• Small daily choices matter over time.
日常的小选择长期来看很重要。""",
    "issue": """issue /ˈɪʃuː/
1. 问题，议题 | An important problem or subject
• The team fixed the issue before launch.
团队在发布前解决了这个问题。

2. 发布，发出 | Officially give or announce something
• The bank issued a new card yesterday.
银行昨天发了一张新卡。""",
    "approach": """approach /əˈproʊtʃ/
1. 方法，方式 | A way of dealing with something
• This approach makes vocabulary review easier.
这种方法让词汇复习更容易。

2. 接近，靠近 | Move nearer to something
• A stranger approached us at the entrance.
一个陌生人在入口处向我们走来。""",
    "account": """account /əˈkaʊnt/
1. 账户 | A record used to access a service or hold money
• I created a new account for the website.
我为这个网站创建了一个新账户。

2. 描述，叙述 | A spoken or written description
• Her account of the event was very clear.
她对这件事的描述非常清楚。""",
    "charge": """charge /tʃɑːrdʒ/
1. 收费 | Ask someone to pay a price
• The hotel charges extra for breakfast.
这家酒店的早餐需要额外收费。

2. 充电 | Put electrical energy into a device
• I charge my phone before going out.
我出门前给手机充电。

3. 指控 | Formally accuse someone
• Police charged him with theft.
警方指控他盗窃。""",
    "figure": """figure /ˈfɪɡjər/
1. 数字 | A number, especially in statistics
• The latest sales figures look promising.
最新的销售数字看起来很不错。

2. 人物 | A person of importance or interest
• She became a leading figure in education.
她成为教育领域的重要人物。

3. 认为，估计 | Think or calculate
• I figured the trip would take two hours.
我估计这趟行程需要两个小时。""",
    "establish": """establish /ɪˈstæblɪʃ/
1. 建立，创立 | Create something intended to last
• They established the company in 2018.
他们在2018年创立了这家公司。

2. 证实，确定 | Prove or discover a fact
• The study established a clear connection.
这项研究证实了一个明确的联系。""",
    "apple": """apple /ˈæpəl/
1. 苹果 | A round fruit with firm flesh
• She sliced an apple for breakfast.
她早餐切了一个苹果。""",
    "book": """book /bʊk/
1. 书，书籍 | A written work with pages
• This book changed how I study English.
这本书改变了我学习英语的方式。

2. 预订 | Arrange to use something later
• We booked a table for Friday evening.
我们预订了周五晚上的餐桌。""",
    "learn": """learn /lɜːrn/
1. 学习，学会 | Gain knowledge or a skill
• Children learn languages through regular practice.
孩子们通过持续练习学习语言。

2. 得知，获悉 | Become aware of a fact
• I learned about the change this morning.
我今天早上得知了这个变化。""",
    "language": """language /ˈlæŋɡwɪdʒ/
1. 语言 | A system of communication using words
• English is widely used as a global language.
英语被广泛用作全球性语言。

2. 措辞，表达方式 | The words used in a particular situation
• Please use simple language in the instructions.
请在说明中使用简单的措辞。""",
    "memory": """memory /ˈmeməri/
1. 记忆力 | The ability to remember
• Regular review strengthens long-term memory.
定期复习可以增强长期记忆。

2. 回忆，记忆 | Something remembered from the past
• That song brought back a childhood memory.
那首歌唤起了她童年的一段回忆。""",
}


def get(term: str) -> str | None:
    # 严格区分大小写：March 不再命中内置的 march。
    return _ENTRIES.get(term.strip())


def count() -> int:
    return len(_ENTRIES)


def sample_terms() -> list[str]:
    return list(_ENTRIES)
