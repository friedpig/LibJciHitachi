import uuid
import logging
import json
import threading
from dataclasses import dataclass, field
from typing import Dict

import httpx
import awscrt
import awsiot
from awsiot import mqtt_connection_builder
from .model import JciHitachiAWSStatus, JciHitachiAWSStatusSupport

AWS_COGNITO_REGION = "ap-northeast-1"
AWS_COGNITO_IDP_ENDPOINT = f"cognito-idp.{AWS_COGNITO_REGION}.amazonaws.com/"
AWS_COGNITO_ENDPOINT = f"cognito-identity.{AWS_COGNITO_REGION}.amazonaws.com/"
AWS_COGNITO_CLIENT_ID = "7kfnjsb66ei1qt5s5gjv6j1lp6"
AWS_COGNITO_USERPOOL_ID = "ap-northeast-1_aTZeaievK"


AWS_IOT_ENDPOINT = "https://iot-api.jci-hitachi-smarthome.com"
AWS_MQTT_ENDPOINT = "a8kcu267h96in-ats.iot.ap-northeast-1.amazonaws.com"
AWS_POOL_ID = "ap-northeast-1:3bb17635-a6c0-431f-b484-b8de026544cf"
AWS_GATEWAY_KEY = "606153a77f347c9b7becfeda37edb2ccde46ce863c7ac"

_LOGGER = logging.getLogger(__name__)

@dataclass
class AWSTokens:
    access_token: str
    id_token: str
    refresh_token: str

@dataclass
class AWSIdentity:
    identity_id: str
    user_name: str
    user_attributes: dict

@dataclass
class AWSCredentials:
    access_key_id: str
    secret_key: str
    session_token: str

@dataclass
class JciHitachiMqttEvents:
    device_status: Dict[str, JciHitachiAWSStatus] = field(default_factory=dict)
    device_support: Dict[str, JciHitachiAWSStatusSupport] = field(default_factory=dict)
    device_control: Dict[str, dict] = field(default_factory=dict)
    #device_access_time: Dict[str, int] = field(default_factory=dict)
    device_status_event: threading.Event = field(default_factory=threading.Event)
    device_support_event: threading.Event = field(default_factory=threading.Event)
    device_control_event: threading.Event = field(default_factory=threading.Event)


class JciHitachiAWSCognitoConnection:
    """Connecting to Jci-Hitachi AWS Cognito API to get data or send commands.

    Parameters
    ----------
    email : str
        User email.
    password : str
        User password.
    aws_tokens : AWSTokens, optional
        If aws_tokens is given, it is used by request;
        otherwise, a login procedure is performed to obtain new aws_tokens,
        by default None.
    proxy : str, optional
        Proxy setting. Format:"IP:port", by default None. 
    print_response : bool, optional
        If set, all responses of httpx will be printed, by default False.
    """
    
    def __init__(self, email, password, aws_tokens=None, proxy=None, print_response=False):
        self._login_response = None
        self._email = email
        self._password = password
        self._print_response = print_response
        self._proxies = {'http': proxy, 'https': proxy} if proxy else None

        if aws_tokens:
            self._aws_tokens = aws_tokens
        else:
            self._aws_tokens = None
            self.login()
    
    def _generate_headers(self, target):
        normal_headers = {
            "X-Amz-Target": target,
            "User-Agent": "Dalvik/2.1.0",
            "content-type": "application/x-amz-json-1.1",
            "Accept" : "application/json",
        }
        return normal_headers

    def _handle_response(self, response):
        response_json = response.json()
        
        if response.status_code == httpx.codes.ok:    
            return "OK", response_json
        else:
            return f"{response['__type']} {response['message']}", response_json

    @property
    def aws_tokens(self):
        return self._aws_tokens

    def login(self, use_refresh_token=False):
        # https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_InitiateAuth.html
        if use_refresh_token and self._aws_tokens != None:
            login_json_data = {
                "AuthFlow": 'REFRESH_TOKEN_AUTH',
                "AuthParameters":{
                    'REFRESH_TOKEN': self._aws_tokens.refresh_token,
                },
                "ClientId": AWS_COGNITO_CLIENT_ID,
            }
        else:
            login_json_data = {
                "AuthFlow": 'USER_PASSWORD_AUTH',
                "AuthParameters": {
                    'USERNAME': self._email,
                    'PASSWORD': self._password,
                },
                "ClientId": AWS_COGNITO_CLIENT_ID,
            }

        login_headers = self._generate_headers("AWSCognitoIdentityProviderService.InitiateAuth")

        login_req = httpx.post("{}".format(f"https://{AWS_COGNITO_IDP_ENDPOINT}"), 
            json=login_json_data,
            headers=login_headers,
            proxies=self._proxies,
        )

        if self._print_response:
            self.print_response(login_req)

        status, response = self._handle_response(login_req)

        if login_req.status_code == httpx.codes.ok:        
            auth_result = response["AuthenticationResult"]
            self._aws_tokens = AWSTokens(
                access_token = auth_result['AccessToken'],
                id_token = auth_result['IdToken'],
                refresh_token = auth_result['RefreshToken'],
            )
        return status, self._aws_tokens

    def get_data(self):
        raise NotImplementedError

    def print_response(self, response):
        print('===================================================')
        print(self.__class__.__name__, 'Response:')
        print('headers:', response.headers)
        print('status_code:', response.status_code)
        print('text:', json.dumps(response.json(), indent=True))
        print('===================================================')


class GetUser(JciHitachiAWSCognitoConnection):
    """API internal endpoint. 
    https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_GetUser.html
    
    Parameters
    ----------
    email : str
        User email.
    password : str
        User password.
    """

    def __init__(self, email, password, **kwargs):
        super().__init__(email, password, **kwargs)
    
    def get_data(self):
        json_data = {
            "AccessToken": self._aws_tokens.access_token,
        }

        headers = self._generate_headers("AWSCognitoIdentityProviderService.GetUser")

        req = httpx.post("{}".format(f"https://{AWS_COGNITO_IDP_ENDPOINT}"), 
            json=json_data,
            headers=headers,
            proxies=self._proxies,
        )

        if self._print_response:
            self.print_response(req)

        status, response = self._handle_response(req)

        if req.status_code == httpx.codes.ok:
            user_attributes = {attr["Name"]: attr["Value"] for attr in response["UserAttributes"]}
            aws_identity = AWSIdentity(
                identity_id = user_attributes["custom:cognito_identity_id"],
                user_name = response["Username"],
                user_attributes = user_attributes,
            )
        return status, aws_identity


class GetCredentials(JciHitachiAWSCognitoConnection):
    """API internal endpoint.
    
    Parameters
    ----------
    email : str
        User email.
    password : str
        User password.
    """

    def __init__(self, email, password, **kwargs):
        super().__init__(email, password, **kwargs)

    def get_data(self, aws_identity):
        json_data = {
            "IdentityId": aws_identity.identity_id,
            "Logins": {
                f"{AWS_COGNITO_IDP_ENDPOINT}{AWS_COGNITO_USERPOOL_ID}": self._aws_tokens.id_token,
            }
        }

        headers = self._generate_headers("AWSCognitoIdentityService.GetCredentialsForIdentity")

        req = httpx.post("{}".format(f"https://{AWS_COGNITO_ENDPOINT}"), 
            json=json_data,
            headers=headers,
            proxies=self._proxies,
        )

        if self._print_response:
            self.print_response(req)

        status, response = self._handle_response(req)

        if req.status_code == httpx.codes.ok:
            aws_credentials = AWSCredentials(
                access_key_id = response['Credentials']['AccessKeyId'],
                secret_key = response['Credentials']['SecretKey'],
                session_token = response['Credentials']['SessionToken'],
            )
        return status, aws_credentials


class JciHitachiAWSIoTConnection:
    """Connecting to Jci-Hitachi AWS Cognito API to get data or send commands.

    Parameters
    ----------
    aws_tokens : AWSTokens, optional
        If aws_tokens is given, it is used by request, 
        otherwise perform a login procedure to obtain new aws_tokens,
        by default None.
    proxy : str, optional
        Proxy setting. Format:"IP:port", by default None. 
    print_response : bool, optional
        If set, all responses of httpx will be printed, by default False.
    """

    def __init__(self, aws_tokens, proxy=None, print_response=False):
        self._aws_tokens = aws_tokens
        self._print_response = print_response
        self._proxies = {'http': proxy, 'https': proxy} if proxy else None
    
    def _generate_normal_headers(self):
        normal_headers = {
            "authorization": f"Bearer {self._aws_tokens.id_token}",
            "accesstoken": f"Bearer {self._aws_tokens.access_token}",
            "User-Agent": "Dalvik/2.1.0",
            "content-type" : "application/json",
            "Accept" : "application/json",
        }
        return normal_headers

    def _handle_response(self, response):
        response_json = response.json()
        code = response_json["status"]["code"]
        if code == 0:
            return code, "OK", response_json
        elif code == 6:
            return code, "Invalid email or password", response_json
        elif code == 12:
            return code, "Invalid session token", response_json
        else:
            return code, "Unknown error", response_json

    def _send(self, api_name, json=None):
        req = httpx.post(
            "{}{}".format(AWS_IOT_ENDPOINT, api_name),
            headers=self._generate_normal_headers(),
            json=json,
            proxies=self._proxies,
        )
        if self._print_response:
            self.print_response(req)

        code, message, response_json = self._handle_response(req)

        return message, response_json

    def get_data(self):
        raise NotImplementedError

    def print_response(self, response):
        print('===================================================')
        print(self.__class__.__name__, 'Response:')
        print('headers:', response.headers)
        print('status_code:', response.status_code)
        print('text:', json.dumps(response.json(), indent=True))
        print('===================================================')


class GetAllDevice(JciHitachiAWSIoTConnection):
    def __init__(self, aws_tokens, **kwargs):
        super().__init__(aws_tokens, **kwargs)

    def get_data(self):
        return self._send("/GetAllDevice")


class GetAllGroup(JciHitachiAWSIoTConnection):
    def __init__(self, aws_tokens, **kwargs):
        super().__init__(aws_tokens, **kwargs)

    def get_data(self):
        return self._send("/GetAllGroup")


class GetAllRegion(JciHitachiAWSIoTConnection):
    def __init__(self, aws_tokens, **kwargs):
        super().__init__(aws_tokens, **kwargs)

    def get_data(self):
        return self._send("/GetAllRegion")


class GetAvailableAggregationMonthlyData(JciHitachiAWSIoTConnection):
    def __init__(self, aws_tokens, **kwargs):
        super().__init__(aws_tokens, **kwargs)

    def get_data(self, thing_name, time_start, time_end):
        json_data = {
            "ThingName": thing_name,
            "TimeStart":time_start,
            "TimeEnd":time_end,
        }
        return self._send("/GetAvailableAggregationMonthlyData", json_data)


class GetHistoryEventByUser(JciHitachiAWSIoTConnection):
    def __init__(self, aws_tokens, **kwargs):
        super().__init__(aws_tokens, **kwargs)

    def get_data(self, time_start, time_end):
        json_data = {
            "TimeStart": time_start,
            "TimeEnd": time_end,
        }
        return self._send("/GetHistoryEventByUser", json_data)



class ListSubUser(JciHitachiAWSIoTConnection):
    def __init__(self, aws_tokens, **kwargs):
        super().__init__(aws_tokens, **kwargs)

    def get_data(self):
        return self._send("/ListSubUser")


class JciHitachiAWSMqttConnection:
    """Connecting to Jci-Hitachi AWS MQTT to get latest events.

    Parameters
    ----------
    email : str
        User email.
    password : str
        User password.
    user_id : int
        User ID.
    print_response : bool, optional
        If set, all responses of MQTT will be printed, by default False.
    """

    def __init__(self, aws_credentials, print_response=False):
        self._cred_provider = awscrt.auth.AwsCredentialsProvider.new_static(
            aws_credentials.access_key_id, 
            aws_credentials.secret_key, 
            aws_credentials.session_token
        )
        self._print_response = print_response
        
        self._mqttc = None
        self._mqtt_events = JciHitachiMqttEvents()

    def __del__(self):
        self.disconnect()
    
    @property
    def mqtt_events(self):
        """MQTT events.

        Returns
        -------
        JciHitachiMqttEvents
            See JciHitachiMqttEvents.
        """

        return self._mqtt_events
    
    def _on_publish(self, topic, payload, dup, qos, retain, **kwargs):
        try:
            payload = json.loads(payload.decode())
        except Exception as e :
            _LOGGER.error(e)

        if self._print_response:
            print(f"Mqtt topic {topic} published with payload \n {payload}")

        splitted_topic = topic.split('/')

        thing_name = splitted_topic[0]

        if len(splitted_topic) >= 3 and splitted_topic[1] == "status" and splitted_topic[2] == "response":
            self._mqtt_events.device_status[thing_name] = JciHitachiAWSStatus(payload)
            self._mqtt_events.device_status_event.set()
        elif len(splitted_topic) >= 3 and splitted_topic[1] == "registration" and splitted_topic[2] == "response":
            self._mqtt_events.device_support[thing_name] = JciHitachiAWSStatusSupport(payload)
            self._mqtt_events.device_support_event.set()
        elif len(splitted_topic) >= 3 and splitted_topic[1] == "control" and splitted_topic[2] == "response":
            self._mqtt_events.device_control[thing_name] = payload
            self._mqtt_events.device_control_event.set()

    def _on_message(self, topic, payload, dup, qos, retain, **kwargs):
        try:
            payload = json.loads(payload.decode())
        except Exception as e :
            _LOGGER.error(e)
        #if self._print_response:
        #    print(f"{topic} {str(payload)}")
        
        splitted_topic = topic.split('/')

        return
        
    
    def configure(self):
        """Configure MQTT.
        """
        event_loop_group = awscrt.io.EventLoopGroup(1)
        host_resolver = awscrt.io.DefaultHostResolver(event_loop_group)
        client_bootstrap = awscrt.io.ClientBootstrap(event_loop_group, host_resolver)
        self._mqttc = mqtt_connection_builder.websockets_with_default_aws_signing(
            AWS_COGNITO_REGION,
            self._cred_provider,
            client_bootstrap=client_bootstrap,
            endpoint=AWS_MQTT_ENDPOINT,
            client_id=str(uuid.uuid4()))
        self._mqttc.on_message(self._on_message)
    
    def connect(self, topics):
        """Connect to the MQTT broker and start loop.
        """
        try:
            connect_future = self._mqttc.connect()
            print(connect_future.result())
            _LOGGER.info("Connected!")
        except Exception as e:
            _LOGGER.error('Connection failed with exception {}'.format(e))

        if isinstance(topics, str):
            topics = [topics]
        
        for topic in topics:
            try:
                subscribe_future, _ = self._mqttc.subscribe(topic, awscrt.mqtt.QoS.AT_LEAST_ONCE, callback=self._on_publish)
                print(subscribe_future.result())
            except Exception as e:
                _LOGGER.error('Subscription failed with exception {}'.format(e))

    def disconnect(self):
        """Disconnect from the MQTT broker.
        """

        self._mqttc.disconnect()

    def publish(self, topic, payload):
        """Publish message.
        """
        publish_future, _ = self._mqttc.publish(topic, json.dumps(payload), awscrt.mqtt.QoS.AT_LEAST_ONCE)
        print(publish_future.result())