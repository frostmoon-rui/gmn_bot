"""Visual guide remains shallow and old topic strings remain copyable."""
import unittest
from io import BytesIO
from PIL import Image
from help_cards import HelpReply, make_reply, render_help


class HelpCardTests(unittest.TestCase):
    def test_two_levels_and_old_topics(self):
        topics={'general':'Original manual','weather':'Basic weather','weather maps':'Maps',
                'local weather charts':'Local charts','community':'Privacy rules','earthquakes':'Reports'}
        menu={'weather':['weather','weather maps','local weather charts'],'community':['community','earthquakes']}
        root=make_reply(topics,menu=menu)
        self.assertIsInstance(root,HelpReply)
        self.assertEqual(root,'Original manual')
        self.assertEqual(len(root.cards),2)
        for topic in menu['weather']:
            reply=make_reply(topics,topic,menu=menu)
            self.assertEqual(reply,topics[topic])
            self.assertEqual(len(reply.cards),3)
        self.assertEqual(type(make_reply(topics,'weather maps',menu=menu,text_only=True)),str)
        image=Image.open(BytesIO(render_help(root)))
        self.assertEqual(image.width,1600)

    def test_unknown_topic_recovers_root(self):
        topics={'general':'Original manual','weather':'Weather'}
        root=make_reply(topics,'unknown',menu={'weather':['weather']})
        self.assertEqual(root,'Original manual')
        self.assertEqual(len(root.cards),1)
