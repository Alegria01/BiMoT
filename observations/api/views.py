 
#from rest_framework.generics import (
   #CreateAPIView,
   #DestroyAPIView,
   #ListAPIView, 
   #UpdateAPIView,
   #RetrieveAPIView,
   #RetrieveUpdateAPIView
#)

from django_filters.rest_framework import DjangoFilterBackend
from django_filters import rest_framework as filters

from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import api_view

from .serializers import SpectrumSerializer, SpectrumListSerializer, SpecFileSerializer,LightCurveSerializer, ObservatorySerializer

from observations.models import Spectrum, SpecFile, LightCurve, Observatory

from observations.aux import read_spectrum, read_lightcurve


# ===============================================================
# Spectrum
# ===============================================================

class SpectrumFilter(filters.FilterSet):
   
   target = filters.CharFilter(field_name="target", method="star_name_icontains", lookup_expr='icontains')
   
   hjd_min = filters.NumberFilter(field_name="hjd", lookup_expr='gte')
   hjd_max = filters.NumberFilter(field_name="hjd", lookup_expr='lte')
   
   exptime_min = filters.NumberFilter(field_name="exptime", lookup_expr='gte')
   exptime_max = filters.NumberFilter(field_name="exptime", lookup_expr='lte')
   
   instrument = filters.CharFilter(field_name="instrument", lookup_expr='icontains')
   
   telescope = filters.CharFilter(field_name="telescope", lookup_expr='icontains')
   
   fluxcal = filters.BooleanFilter(field_name='fluxcal')
   
   def star_name_icontains(self, queryset, name, value):
      return queryset.filter(star__name__icontains=value)
   
   class Meta:
      model = Spectrum
      fields = ['project',]
      
   @property
   def qs(self):
      parent = super(SpectrumFilter, self).qs
      #user = getattr(self.request, 'user', None)
      
      # get the column order from the GET dictionary
      getter = self.request.query_params.get
      if not getter('order[0][column]') is None:
         order_column = int(getter('order[0][column]'))
         order_name = getter('columns[%i][data]' % order_column)
         if getter('order[0][dir]') == 'desc': order_name = '-'+order_name
         
         return parent.order_by(order_name)
      else:
         return parent
      

class SpectrumViewSet(viewsets.ModelViewSet):
   queryset = Spectrum.objects.all()
   serializer_class = SpectrumSerializer
   
   filter_backends = (DjangoFilterBackend,)
   filterset_class = SpectrumFilter

# ===============================================================
# SpecFile
# ===============================================================

class SpecFileFilter(filters.FilterSet):
   
   target = filters.CharFilter(field_name="target", method="star_name_icontains", lookup_expr='icontains')
   
   hjd_min = filters.NumberFilter(field_name="hjd", lookup_expr='gte')
   hjd_max = filters.NumberFilter(field_name="hjd", lookup_expr='lte')
   
   instrument = filters.CharFilter(field_name="instrument", lookup_expr='icontains')
   
   #processed = filters.BooleanFilter(field_name="Processed", method="is_processed")
   
   def star_name_icontains(self, queryset, name, value):
      return queryset.filter(spectrum__star__name__icontains=value)
   
   #def is_processed(self, queryset, name, value):
      #return False
   
   class Meta:
      model = SpecFile
      fields = ['project',]
      
   @property
   def qs(self):
      parent = super(SpecFileFilter, self).qs
      #user = getattr(self.request, 'user', None)
      
      # get the column order from the GET dictionary
      getter = self.request.query_params.get
      if not getter('order[0][column]') is None:
         order_column = int(getter('order[0][column]'))
         order_name = getter('columns[%i][data]' % order_column)
         if getter('order[0][dir]') == 'desc': order_name = '-'+order_name
         
         return parent.order_by(order_name)
      else:
         return parent

class SpecFileViewSet(viewsets.ModelViewSet):
   queryset = SpecFile.objects.all()
   serializer_class = SpecFileSerializer
   
   filter_backends = (DjangoFilterBackend,)
   filterset_class = SpecFileFilter



@api_view(['POST'])
def processSpecfile(request, specfile_pk):
   success, message = read_spectrum.process_specfile(specfile_pk)
   specfile = SpecFile.objects.get(pk=specfile_pk)
   
   return Response(SpecFileSerializer(specfile).data)

@api_view(['GET'])
def getSpecfileHeader(request, specfile_pk):
   specfile = SpecFile.objects.get(pk=specfile_pk)
   header = specfile.get_header()
   
   return Response(header)


# ===============================================================
# LightCurve
# ===============================================================

class LightCurveViewSet(viewsets.ModelViewSet):
   queryset = LightCurve.objects.all()
   serializer_class = LightCurveSerializer
   
   #filter_backends = (DjangoFilterBackend,)
   #filterset_class = SpecFileFilter


@api_view(['POST'])
def processLightCurve(request, lightcurve_pk):
   success, message = read_lightcurve.process_lightcurve(lightcurve_pk)
   lightcurve = SpecFile.objects.get(pk=lightcurve_pk)
   
   return Response(LightCurveSerializer(lightcurve).data)

# ===============================================================
# Observatory
# ===============================================================

class ObservatoryFilter(filters.FilterSet):
   
   name = filters.CharFilter(field_name="name", lookup_expr='icontains')
   
   class Meta:
      model = Observatory
      fields = ['latitude', 'longitude', 'altitude', 'project']


class ObservatoryViewSet(viewsets.ModelViewSet):
   queryset = Observatory.objects.all()
   serializer_class = ObservatorySerializer
   
   filter_backends = (DjangoFilterBackend,)
   filterset_class = ObservatoryFilter
   
   
