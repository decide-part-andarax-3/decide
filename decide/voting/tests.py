import random
import itertools
import time
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.test import APITestCase
from django.core.exceptions import ValidationError
from django.contrib.staticfiles.testing import StaticLiveServerTestCase

from base import mods
from base.tests import BaseTestCase
from census.models import Census
from mixnet.mixcrypt import ElGamal
from mixnet.mixcrypt import MixCrypt
from mixnet.models import Auth
from voting.models import Voting, Question, QuestionOption, QuestionOrder

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

class VotingTestCase(BaseTestCase):

    def setUp(self):
        super().setUp()

    def tearDown(self):
        super().tearDown()

    def encrypt_msg(self, msg, v, bits=settings.KEYBITS):
        pk = v.pub_key
        p, g, y = (pk.p, pk.g, pk.y)
        k = MixCrypt(bits=bits)
        k.k = ElGamal.construct((p, g, y))
        return k.encrypt(msg)

    def create_voting(self):
        q = Question(desc='test question')
        q.save()
        for i in range(5):
            opt = QuestionOption(question=q, option='option {}'.format(i+1))
            opt.save()
        v = Voting(name='test voting', question=q)
        v.save()

        a, _ = Auth.objects.get_or_create(url=settings.BASEURL,
                                          defaults={'me': True, 'name': 'test auth'})
        a.save()
        v.auths.add(a)

        return v

    def create_order_voting(self):
        q = Question(desc='test ordering question')
        q.save()
        for i in range(5):
            opt = QuestionOrder(question=q, option='ordering option {}'.format(i+1), order_number='{}'.format(i+1))
            opt.save()
        v = Voting(name='test ordering voting', question=q)
        v.save()

        a, _ = Auth.objects.get_or_create(url=settings.BASEURL,
                                          defaults={'me': True, 'name': 'test auth'})
        a.save()
        v.auths.add(a)

        return v

    def create_voters(self, v):
        for i in range(100):
            u, _ = User.objects.get_or_create(username='testvoter{}'.format(i))
            u.is_active = True
            u.save()
            c = Census(voter_id=u.id, voting_id=v.id)
            c.save()

    def get_or_create_user(self, pk):
        user, _ = User.objects.get_or_create(pk=pk)
        user.username = 'user{}'.format(pk)
        user.set_password('qwerty')
        user.save()
        return user

    def test_voting_toString(self):
        v = self.create_voting()
        self.assertEquals(str(v), "test voting")
        self.assertEquals(str(v.question), "test question")
        self.assertEquals(str(v.question.options.all()[0]), "option 1 (2)")

    def test_update_voting_400(self):
        v = self.create_voting()
        data = {} #El campo action es requerido en la request
        self.login()
        response = self.client.put('/voting/{}/'.format(v.pk), data, format= 'json')
        self.assertEquals(response.status_code, 400)

    def store_votes(self, v):
        voters = list(Census.objects.filter(voting_id=v.id))
        voter = voters.pop()

        clear = {}
        for opt in v.question.options.all():
            clear[opt.number] = 0
            for i in range(random.randint(0, 5)):
                a, b = self.encrypt_msg(opt.number, v)
                data = {
                    'voting': v.id,
                    'voter': voter.voter_id,
                    'vote': { 'a': a, 'b': b },
                }
                clear[opt.number] += 1
                user = self.get_or_create_user(voter.voter_id)
                self.login(user=user.username)
                voter = voters.pop()
                mods.post('store', json=data)
        return clear

    def test_complete_voting(self):
        v = self.create_voting()
        self.create_voters(v)

        v.create_pubkey()
        v.start_date = timezone.now()
        v.save()

        clear = self.store_votes(v)

        self.login()  # set token
        v.tally_votes(self.token)

        tally = v.tally
        tally.sort()
        tally = {k: len(list(x)) for k, x in itertools.groupby(tally)}

        for q in v.question.options.all():
            self.assertEqual(tally.get(q.number, 0), clear.get(q.number, 0))

        for q in v.postproc:
            self.assertEqual(tally.get(q["number"], 0), q["votes"])

    def test_create_voting_from_api(self):
        data = {'name': 'Example'}
        response = self.client.post('/voting/', data, format='json')
        self.assertEqual(response.status_code, 401)

        # login with user no admin
        self.login(user='noadmin')
        response = mods.post('voting', params=data, response=True)
        self.assertEqual(response.status_code, 403)

        # login with user admin
        self.login()
        response = mods.post('voting', params=data, response=True)
        self.assertEqual(response.status_code, 400)

        data = {
            'name': 'Example',
            'desc': 'Description example',
            'question': 'I want a ',
            'question_opt': ['cat', 'dog', 'horse'],
            'question_ord': []
        }

        response = self.client.post('/voting/', data, format='json')
        self.assertEqual(response.status_code, 201)

    def test_create_ordering_voting_from_api(self):
        data = {'name': 'Example'}

        self.login()
        response = mods.post('voting', params=data, response=True)
        self.assertEqual(response.status_code, 400)

        data = {
            'name': 'Example',
            'desc': 'Description example',
            'question': 'I want a ',
            'question_opt': [],
            'question_ord': ['cat', 'dog', 'horse']
        }

        response = self.client.post('/voting/', data, format='json')
        self.assertEqual(response.status_code, 201)

    def test_update_voting(self):
        voting = self.create_voting()

        data = {'action': 'start'}
        #response = self.client.post('/voting/{}/'.format(voting.pk), data, format='json')
        #self.assertEqual(response.status_code, 401)

        # login with user no admin
        self.login(user='noadmin')
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 403)

        # login with user admin
        self.login()
        data = {'action': 'bad'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)

        # STATUS VOTING: not started
        for action in ['stop', 'tally']:
            data = {'action': action}
            response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), 'Voting is not started')

        data = {'action': 'start'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), 'Voting started')

        # STATUS VOTING: started
        data = {'action': 'start'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), 'Voting already started')

        data = {'action': 'tally'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), 'Voting is not stopped')

        data = {'action': 'stop'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), 'Voting stopped')

        # STATUS VOTING: stopped
        data = {'action': 'start'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), 'Voting already started')

        data = {'action': 'stop'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), 'Voting already stopped')

        data = {'action': 'tally'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), 'Voting tallied')

        # STATUS VOTING: tallied
        data = {'action': 'start'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), 'Voting already started')

        data = {'action': 'stop'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), 'Voting already stopped')

        data = {'action': 'tally'}
        response = self.client.put('/voting/{}/'.format(voting.pk), data, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), 'Voting already tallied')


class VotingModelTestCase(BaseTestCase):

    def setUp(self):
        q1 = Question(desc='This is a test yes/no question', is_yes_no_question=True)
        q1.save()

        q2 = Question(desc='This is NOT a test yes/no question', is_yes_no_question=False)
        q2.save()

        q3 = Question(desc='This contain an order question', is_yes_no_question=False)
        q3.save()

        qo1 = QuestionOption(question = q2, option = 'Primera opcion')
        qo1.save()

        qo2 = QuestionOption(question = q2, option = 'Segunda opcion')
        qo2.save()

        qo3 = QuestionOption(question = q2, option = 'Tercera opcion')
        qo3.save()

        qord1 = QuestionOrder(question = q3, option = 'First order')
        qord1.save()

        super().setUp()

    def tearDown(self):
        super().tearDown()

    def test_delete_when_unselect(self):
        q = Question.objects.get(desc='This is a test yes/no question')
        q.is_yes_no_question = False
        q.save()

        self.assertEquals(len(q.options.all()), 0)

    # don't save others YES/NO if it saves question with YES/NO question selected
    def test_duplicity_yes_and_no(self):
        q = Question.objects.get(desc='This is a test yes/no question')
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # duplicate YES option
    def test_duplicity_yes(self):
        q = Question.objects.get(desc='This is a test yes/no question')
        qo = QuestionOption(question = q, number = 2, option = 'YES')
        qo.save()
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # duplicate NO option
    def test_duplicity_no(self):
        q = Question.objects.get(desc='This is a test yes/no question')
        qo = QuestionOption(question = q, number = 2, option = 'NO')
        qo.save()
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # verify when selected YES/NO options that adds these
    def add_yes_no_question(self):
        q = Question.objects.get(desc='This is NOT a test yes/no question')
        q.is_yes_no_question = True
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # add some option before and don't add this one if YES/NO is selected
    def add_before_yes_no(self):
        q = Question.objects.get(desc='This is a test yes/no question')
        qo = QuestionOption(question = q, number = 2, option = 'Something')
        qo.save()
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # previous options are deleted when question is saved like YES/NO question
    def test_delete_previous_opt(self):
        q = Question.objects.get(desc='This is NOT a test yes/no question')
        q.is_yes_no_question = True
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # delete NO option, save and returns YES and NO option
    def test_delete_no_with_yes_no_selected(self):
        q = Question.objects.get(desc='This is a test yes/no question')
        qo = QuestionOption.objects.get(option = 'NO', question = q)
        QuestionOption.delete(qo)
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # delete YES option, save and returns YES and NO option
    def test_delete_yes_with_yes_no_selected(self):
        q = Question.objects.get(desc='This is a test yes/no question')
        qo = QuestionOption.objects.get(option = 'YES', question = q)
        QuestionOption.delete(qo)
        q.save()

        self.assertEquals(len(q.options.all()), 2)
        self.assertEquals(q.options.all()[0].question, q)
        self.assertEquals(q.options.all()[1].question, q)
        self.assertEquals(q.options.all()[0].option, 'YES')
        self.assertEquals(q.options.all()[1].option, 'NO')
        self.assertEquals(q.options.all()[0].number, 0)
        self.assertEquals(q.options.all()[1].number, 1)

    # question cannot contain 2 different options with the same "name"
    def test_duplicity_option(self):
        q = Question.objects.get(desc='This is NOT a test yes/no question')
        qo = QuestionOption(question = q, option = 'Primera opcion')
        qo.save()
        q.save()

        self.assertRaises(ValidationError)
        self.assertRaisesRegex(ValidationError,"Duplicated option, please checkout question options")
        self.assertEquals(len(q.options.all()), 3)

    # question cannot contain 2 different order with the same "name"
    def test_duplicity_order(self):
        q = Question.objects.get(desc='This contain an order question')
        qo = QuestionOrder(question = q, option = 'First order')
        qo.save()
        q.save()

        self.assertRaises(ValidationError)
        self.assertRaisesRegex(ValidationError,"Duplicated order, please checkout question order")
        self.assertEquals(len(q.order_options.all()), 1)

class VotingViewTestCase(StaticLiveServerTestCase):

    def setUp(self):
        
        # Load base test functionality for decide
        self.base = BaseTestCase()
        self.base.setUp()

        # self.vars = {}

        options = webdriver.ChromeOptions()
        options.headless = True
        self.driver = webdriver.Chrome(options=options)
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.driver.quit()

        self.base.tearDown()

    def wait_for_window(self, timeout = 2):
        time.sleep(round(timeout / 1000))
        wh_now = self.driver.window_handles
        wh_then = self.vars["window_handles"]
        if len(wh_now) > len(wh_then):
            return set(wh_now).difference(set(wh_then)).pop()


    def test_duplicity_yes(self):

        # add the user and login
        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Test duplicity with yes")
        driver.find_element_by_id("id_is_yes_no_question").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test duplicity with yes')])[2]").click()
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))
        driver.find_element_by_id("id_options-2-option").clear()
        driver.find_element_by_id("id_options-2-option").send_keys("YES")
        driver.find_element_by_xpath("//tr[@id='options-2']/td[4]").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test duplicity with yes')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))


    def test_delete_when_unselect(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Delete when unselected")
        driver.find_element_by_xpath("//form[@id='question_form']/div/fieldset/div[2]/div/label").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Delete when unselected')])[2]").click()
        driver.find_element_by_xpath("//form[@id='question_form']/div/fieldset/div[2]/div/label").click()
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Delete when unselected')])[2]").click()
        self.assertEqual("", driver.find_element_by_id("id_options-0-option").get_attribute("value"))


    def test_delete_previous_opt(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)
        
        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Delete options")
        driver.find_element_by_id("id_options-0-option").click()
        driver.find_element_by_id("id_options-0-option").clear()
        driver.find_element_by_id("id_options-0-option").send_keys("First Option")
        driver.find_element_by_id("id_options-1-option").click()
        driver.find_element_by_id("id_options-1-option").clear()
        driver.find_element_by_id("id_options-1-option").send_keys("Second Option")
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Delete options')])[2]").click()
        driver.find_element_by_id("id_is_yes_no_question").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Delete options')])[2]").click()
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertNotRegexpMatches(driver.find_element_by_css_selector("BODY").text, r"^[\s\S]*id=id_options-2-option[\s\S]*$")
        self.assertNotRegexpMatches(driver.find_element_by_css_selector("BODY").text, r"^[\s\S]*id=id_options-3-option[\s\S]*$")
        self.assertNotRegexpMatches(driver.find_element_by_css_selector("BODY").text, r"^[\s\S]*id=id_options-4-option[\s\S]*$")


    def test_duplicity_no(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Test duplicity with yes")
        driver.find_element_by_id("id_is_yes_no_question").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test duplicity with yes')])[2]").click()
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))
        driver.find_element_by_id("id_options-2-option").clear()
        driver.find_element_by_id("id_options-2-option").send_keys("NO")
        driver.find_element_by_xpath("//tr[@id='options-2']/td[4]").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test duplicity with yes')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))


    def test_duplicity_yes_and_no(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Duplicity")
        driver.find_element_by_xpath("//form[@id='question_form']/div/fieldset/div[2]/div/label").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Duplicity')])[2]").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Duplicity')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))


    def test_delete_yes_with_yes_no_selected(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Test delete yes with YES/NO selected")
        driver.find_element_by_id("id_is_yes_no_question").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test delete yes with YES/NO selected')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))
        driver.find_element_by_id("id_options-0-DELETE").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test delete yes with YES/NO selected')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))


    def test_delete_no_with_yes_no_selected(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Test delete no with YES/NO selected")
        driver.find_element_by_id("id_is_yes_no_question").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test delete no with YES/NO selected')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))
        driver.find_element_by_id("id_options-1-DELETE").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test delete no with YES/NO selected')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))

    def test_add_before_yes_no(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Test add before YES/NO question")
        driver.find_element_by_id("id_is_yes_no_question").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test add before YES/NO question')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))
        driver.find_element_by_id("id_options-2-option").clear()
        driver.find_element_by_id("id_options-2-option").send_keys("Something")
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test add before YES/NO question')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))


    def add_yes_no_question(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Test add YES/NO question")
        driver.find_element_by_id("id_is_yes_no_question").click()
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test add YES/NO question')])[2]").click()
        self.assertEqual("0", driver.find_element_by_id("id_options-0-number").get_attribute("value"))
        self.assertEqual("YES", driver.find_element_by_id("id_options-0-option").text)
        self.assertEqual("1", driver.find_element_by_id("id_options-1-number").get_attribute("value"))
        self.assertEqual("NO", driver.find_element_by_id("id_options-1-option").text)
        self.assertEqual("", driver.find_element_by_id("id_options-2-option").get_attribute("value"))


    def test_duplicity_order(self):

        User.objects.create_superuser('superuser', 'superuser@decide.com', 'superuser')
        self.driver.get(f'{self.live_server_url}/admin/')
        self.driver.find_element_by_id('id_username').send_keys("superuser")
        self.driver.find_element_by_id('id_password').send_keys("superuser", Keys.ENTER)

        driver = self.driver
        driver.find_element_by_xpath("(//a[contains(text(),'Add')])[9]").click()
        driver.find_element_by_id("id_desc").clear()
        driver.find_element_by_id("id_desc").send_keys("Test duplicity order name")
        driver.find_element_by_id("id_order_options-0-option").click()
        driver.find_element_by_xpath("//tr[@id='order_options-0']/td[4]").click()
        driver.find_element_by_id("id_order_options-0-option").clear()
        driver.find_element_by_id("id_order_options-0-option").send_keys("Hi Pepito")
        driver.find_element_by_id("id_order_options-1-option").click()
        driver.find_element_by_id("id_order_options-1-option").clear()
        driver.find_element_by_id("id_order_options-1-option").send_keys("Hi Pepito")
        driver.find_element_by_name("_save").click()
        driver.find_element_by_xpath("(//a[contains(text(),'Test duplicity order name')])[2]").click()
        self.assertEqual("Hi Pepito", driver.find_element_by_id("id_order_options-0-option").text)
        self.assertEqual("", driver.find_element_by_id("id_order_options-1-option").get_attribute("value"))

