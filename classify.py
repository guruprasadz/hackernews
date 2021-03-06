import os
import datetime
import json
import threading
from time import sleep

from firebase import firebase
from monkeylearn import MonkeyLearn

from pymongo import MongoClient

from utils import get_link_content


if 'MONKEYLEARN_APIKEY' not in os.environ:
    raise Exception("Monkeylearn token is required")

MAX_RETRIES = 5

MONGO_URI, MONGO_DB = os.environ['MONGO_URI'].rsplit('/', 1)

MONKEYLEARN_TOKEN = os.environ['MONKEYLEARN_APIKEY']
MONKEYLEARN_MODULE_ID = 'cl_GLSChuJQ'

firebase = firebase.FirebaseApplication(
    'https://hacker-news.firebaseio.com',
    authentication=None
)

db = MongoClient(MONGO_URI)[MONGO_DB]


def update_post(post, cached_post, ranking):
    update = {'$set': {}}
    # Update ranking position
    if cached_post['ranking'] != ranking:
        update['$set']['ranking'] = ranking
        # Update ranking of posts that had this position previously
        db.posts.update({'ranking': ranking}, {'$set': {'ranking': None}})

    # Update post comments count
    if 'descendants' in post\
       and cached_post['ranking'] != post['descendants']:
        update['$set']['comments'] = post['descendants']

    # Update post score
    if cached_post['score'] != post['score']:
        update['$set']['score'] = post['score']

    if update['$set']:
        db.posts.update({'id': cached_post['id']}, update)


def get_hn_post(post_id):
    result = None
    fail_count = 0
    while (not result and fail_count <= MAX_RETRIES):
        try:
            result = firebase.get('/v0/item/%s' % post_id, None)
        except:
            fail_count += 1
            sleep(2)
            continue
    return result


def classify_top_posts(max_posts=None):
    top_posts_ids = firebase.get('/v0/topstories', None)

    new_posts = []

    for i, post_id in enumerate(top_posts_ids):
        ranking = i + 1
        post = get_hn_post(post_id)
        cached_post = db.posts.find_one({'id': post_id})

        print u'#{} Procesing {} ("{}")'.format(ranking, post['id'], post['title'])

        if cached_post:
            print '----> Already classified, updating...'
            update_post(post, cached_post, ranking)
        else:
            if post and 'url' in post:
                text = get_link_content(post['url'])
                post_data = {
                    'id': post_id,
                    'url': post['url'],
                    'title': post['title'],
                    'text': text,
                    'time': datetime.datetime .fromtimestamp(int(post['time'])),
                    'score': post['score'],
                    'username': post['by'],
                    'ranking': ranking
		}
                if 'descendants'in post:
                    post_data['comments'] = post['descendants']

		if text and text.strip():
                    # Has good text, queue for classification...
                    print '----> Queuing for classification...'
                    new_posts.append(post_data)
		else:
                    print '----> Unclassifiable, inserting as random...'
                    post_data['result'] = {
			'label': 'random',
			'probability': '--'
                    }
                    post_data['original_result'] = None
                    db.posts.insert(post_data)
	if max_posts and ranking >= max_posts:
            break

    # Classify posts
    if new_posts:
        print "Classifying {} queued posts with MonkeyLearn".format(len(new_posts))
        ml = MonkeyLearn(MONKEYLEARN_TOKEN)
        result = ml.classifiers.classify(
            MONKEYLEARN_MODULE_ID,
            (p['text'] for p in new_posts)
        ).result

        # Add classification data to new posts and save to db
        for i, post in enumerate(new_posts):
            if result[i][0]['probability'] > 0.5:
                post['result'] = result[i][0]
            else:
                post['result'] = {
                    'label': 'random',
                    'probability': '--'
                }
            post['original_result'] = result[i][0]

            print post['ranking'], post['title']
            db.posts.insert(post)

    # Delete old posts
    db.posts.delete_many({'id': {'$nin': top_posts_ids}})


if __name__ == '__main__':
    max_posts = os.environ.get('HN_MAX_POSTS', None)
    if max_posts:
        max_posts = int(max_posts)
    classify_top_posts(max_posts)
